using Microsoft.Extensions.DependencyInjection;
using OpenTelemetry.Exporter;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

namespace PharmaGo.UsersService.Factory;

public static class OpenTelemetryExtensions
{
    public static IServiceCollection AddPharmaGoOpenTelemetryMetrics(
        this IServiceCollection services, string serviceName = "PharmaGo.UsersService")
    {
        var endpoint = new Uri(Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
            ?? "http://otlp-collector:4317");
        var resource = ResourceBuilder.CreateDefault().AddService(serviceName);

        services.AddOpenTelemetry()
            .WithMetrics(metrics =>
            {
                metrics.SetResourceBuilder(resource)
                    .AddAspNetCoreInstrumentation()
                    .AddHttpClientInstrumentation()
                    .AddMeter("PharmaGo.CustomMetrics")
                    .AddPrometheusExporter()
                    .AddOtlpExporter(options =>
                    {
                        options.Endpoint = endpoint;
                        options.Protocol = OtlpExportProtocol.Grpc;
                    });
            })
            .WithTracing(traces =>
            {
                traces.SetResourceBuilder(resource)
                    .AddAspNetCoreInstrumentation()
                    .AddHttpClientInstrumentation()
                    .AddOtlpExporter(options =>
                    {
                        options.Endpoint = endpoint;
                        options.Protocol = OtlpExportProtocol.Grpc;
                    });
            });

        return services;
    }
}
