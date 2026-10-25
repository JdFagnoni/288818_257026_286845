using Microsoft.Extensions.Logging;

namespace Instrumentation;

public static class JsonConsoleLoggingExtensions
{
    public static ILoggingBuilder UseStructuredJsonConsole(this ILoggingBuilder logging)
    {
        logging.ClearProviders();
        logging.AddJsonConsole(options =>
        {
            options.IncludeScopes = true;
            options.TimestampFormat = "yyyy-MM-ddTHH:mm:ss.fffZ";
            options.UseUtcTimestamp = true;
        });
        return logging;
    }
}
