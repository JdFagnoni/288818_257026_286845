using AspNetCoreRateLimit;
using Microsoft.Extensions.Options;

namespace PharmaGo.ApiGateway.Middleware
{
    // AspNetCoreRateLimit toma la IP del header X-Real-IP si viene en el request
    // (es su valor por defecto aunque no se configure RealIpHeader). Ese header
    // lo controla el cliente: cambiándolo en cada request se evade el límite.
    // Ningún proxy de confianza lo agrega delante del gateway, así que solo se
    // usa la IP de la conexión TCP, que el cliente no puede falsificar.
    public class ConnectionIpRateLimitConfiguration : RateLimitConfiguration
    {
        public ConnectionIpRateLimitConfiguration(
            IOptions<IpRateLimitOptions> ipOptions,
            IOptions<ClientRateLimitOptions> clientOptions)
            : base(ipOptions, clientOptions)
        {
        }

        public override void RegisterResolvers()
        {
            base.RegisterResolvers();
            IpResolvers.Clear();
            IpResolvers.Add(new IpConnectionResolveContributor());
        }
    }
}
