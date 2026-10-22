using System.Collections.Concurrent;
using System.Net;
using Microsoft.Extensions.Http;
using Microsoft.Extensions.Options;
using Polly;
using Polly.CircuitBreaker;
using Polly.Timeout;
using Yarp.ReverseProxy.Forwarder;

namespace PharmaGo.ApiGateway.Resilience
{
    public class ResilienceOptions
    {
        public int AttemptTimeoutSeconds { get; set; } = 5;
        public int RetryCount { get; set; } = 2;
        public int RetryBaseDelayMilliseconds { get; set; } = 100;
        public int RetryMaxJitterMilliseconds { get; set; } = 100;
        public int CircuitBreakerFailuresBeforeBreaking { get; set; } = 5;
        public int CircuitBreakerBreakSeconds { get; set; } = 5;
    }

    // Agrega circuit breaker, retry y timeout al HttpClient que YARP usa para
    // reenviar cada request a users-service y pharmacy-service. YARP crea un
    // cliente por cluster, así que cada microservicio tiene su propio circuito:
    // si pharmacy falla, las rutas de users siguen funcionando.
    public class ResilientForwarderHttpClientFactory : ForwarderHttpClientFactory
    {
        private static readonly HashSet<HttpMethod> IdempotentMethods = new()
        {
            HttpMethod.Get, HttpMethod.Head, HttpMethod.Options
        };

        private readonly ILogger<ResilientForwarderHttpClientFactory> _logger;
        private readonly ResilienceOptions _options;

        // Las políticas se guardan por cluster para que el estado del circuito
        // sobreviva cuando YARP recrea el cliente al recargar la configuración.
        private readonly ConcurrentDictionary<string, ClusterPolicies> _policies = new();

        public ResilientForwarderHttpClientFactory(
            ILogger<ResilientForwarderHttpClientFactory> logger,
            IOptions<ResilienceOptions> options)
            : base(logger)
        {
            _logger = logger;
            _options = options.Value;
        }

        protected override HttpMessageHandler WrapHandler(ForwarderHttpClientContext context, HttpMessageHandler handler)
        {
            var policies = _policies.GetOrAdd(context.ClusterId, BuildPolicies);

            // Solo se reintentan métodos idempotentes: repetir un POST podría
            // duplicar una compra o un pedido de stock.
            return new PolicyHttpMessageHandler(request =>
                IdempotentMethods.Contains(request.Method) ? policies.WithRetry : policies.WithoutRetry)
            {
                InnerHandler = base.WrapHandler(context, handler)
            };
        }

        private ClusterPolicies BuildPolicies(string clusterId)
        {
            // Si el servicio no responde en este tiempo, el intento cuenta como
            // fallo en vez de dejar el request colgado hasta el timeout de YARP (100s).
            var timeout = Policy.TimeoutAsync<HttpResponseMessage>(_options.AttemptTimeoutSeconds);

            // Tras N fallos de infraestructura seguidos el circuito se abre y los
            // requests fallan al instante durante CircuitBreakerBreakSeconds; después
            // deja pasar uno de prueba.
            var circuitBreaker = InfrastructureFailures()
                .CircuitBreakerAsync(
                    _options.CircuitBreakerFailuresBeforeBreaking,
                    TimeSpan.FromSeconds(_options.CircuitBreakerBreakSeconds),
                    onBreak: (outcome, breakDelay) => _logger.LogWarning(
                        "Circuit breaker ABIERTO para {ClusterId} durante {BreakSeconds}s tras {Failures} fallos seguidos. Último fallo: {Reason}",
                        clusterId, breakDelay.TotalSeconds, _options.CircuitBreakerFailuresBeforeBreaking, DescribeOutcome(outcome)),
                    onReset: () => _logger.LogInformation(
                        "Circuit breaker CERRADO para {ClusterId}: el servicio volvió a responder", clusterId),
                    onHalfOpen: () => _logger.LogInformation(
                        "Circuit breaker SEMIABIERTO para {ClusterId}: se deja pasar un request de prueba", clusterId));

            // Backoff exponencial (100ms, 200ms, ...) más jitter aleatorio, para que
            // las réplicas del gateway no reintenten todas al mismo tiempo. Que el
            // reintento llegue al pod sano no está garantizado: YARP llama al Service
            // y kube-proxy elige el pod al azar en cada conexión nueva. Si un pod
            // muere de golpe, los reintentos pueden volver a caer en él hasta que
            // Kubernetes lo saca de los endpoints (menos de 1s en las pruebas).
            var retry = InfrastructureFailures()
                .WaitAndRetryAsync(
                    _options.RetryCount,
                    attempt => TimeSpan.FromMilliseconds(
                        _options.RetryBaseDelayMilliseconds * Math.Pow(2, attempt - 1)
                        + Random.Shared.Next(0, _options.RetryMaxJitterMilliseconds)),
                    onRetry: (outcome, delay, attempt, _) => _logger.LogWarning(
                        "Reintento {Attempt}/{RetryCount} hacia {ClusterId} en {DelayMs}ms. Motivo: {Reason}",
                        attempt, _options.RetryCount, clusterId, (int)delay.TotalMilliseconds, DescribeOutcome(outcome)));

            // Con el circuito abierto se responde 503 sin llamar al servicio, en vez
            // del 502 genérico de YARP, para que el cliente sepa que puede reintentar.
            var openCircuitFallback = Policy<HttpResponseMessage>
                .Handle<BrokenCircuitException>()
                .FallbackAsync(_ => Task.FromResult(CircuitOpenResponse(clusterId)));

            return new ClusterPolicies(
                WithRetry: Policy.WrapAsync(openCircuitFallback, retry, circuitBreaker, timeout),
                WithoutRetry: Policy.WrapAsync(openCircuitFallback, circuitBreaker, timeout));
        }

        // Solo cuentan las fallas de infraestructura: errores de red, timeouts y
        // 502/503/504. Los servicios devuelven 503 cuando no responde la base de
        // datos. Un 500 es un error del código de un endpoint: no se reintenta
        // (volvería a fallar igual) y no abre el circuito, para que un endpoint
        // roto no deje sin servicio al resto de las rutas del microservicio.
        private static PolicyBuilder<HttpResponseMessage> InfrastructureFailures() =>
            Policy<HttpResponseMessage>
                .Handle<HttpRequestException>()
                .Or<TimeoutRejectedException>()
                .OrResult(response => response.StatusCode is HttpStatusCode.BadGateway
                    or HttpStatusCode.ServiceUnavailable
                    or HttpStatusCode.GatewayTimeout);

        private HttpResponseMessage CircuitOpenResponse(string clusterId)
        {
            var response = new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
            {
                Content = new StringContent(
                    $"{{\"error\":\"Service unavailable\",\"message\":\"Circuit breaker open for {clusterId}\"}}",
                    System.Text.Encoding.UTF8,
                    "application/json")
            };
            response.Headers.RetryAfter = new System.Net.Http.Headers.RetryConditionHeaderValue(
                TimeSpan.FromSeconds(_options.CircuitBreakerBreakSeconds));
            response.Headers.Add("X-Circuit-Breaker", "open");
            return response;
        }

        private static string DescribeOutcome(DelegateResult<HttpResponseMessage> outcome) =>
            outcome.Exception != null
                ? $"{outcome.Exception.GetType().Name}: {outcome.Exception.Message}"
                : $"HTTP {(int)outcome.Result.StatusCode}";

        private record ClusterPolicies(
            IAsyncPolicy<HttpResponseMessage> WithRetry,
            IAsyncPolicy<HttpResponseMessage> WithoutRetry);
    }
}
