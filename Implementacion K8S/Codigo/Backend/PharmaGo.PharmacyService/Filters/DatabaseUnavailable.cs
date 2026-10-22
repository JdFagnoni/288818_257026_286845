using Microsoft.Data.SqlClient;

namespace PharmaGo.PharmacyService.Filters;

// Distingue "falló mi código" (500) de "no responde la base de datos" (503).
// El gateway cuenta los 503 para abrir el circuit breaker y no los 500, así
// que un endpoint con un bug no corta todo el servicio, pero una caída de la
// base sí.
public static class DatabaseUnavailable
{
    public const string Message = "Database unavailable";

    public static bool Matches(Exception exception)
    {
        // EF puede envolver la SqlException (por ejemplo en DbUpdateException),
        // así que se recorre toda la cadena de InnerException.
        for (var e = exception; e != null; e = e.InnerException)
        {
            // Severidad 20 o más: error de conexión (servidor caído, red, conexión
            // cortada). -2: timeout de la consulta. 4060: no se pudo abrir la base.
            // Las violaciones de constraints o los errores de sintaxis tienen
            // severidad menor y siguen dando 500.
            if (e is SqlException sql && (sql.Class >= 20 || sql.Number is -2 or 4060))
                return true;
        }
        return false;
    }
}

// El ExceptionFilter solo atrapa lo que se lanza dentro de la acción. Cuando un
// controller devuelve una consulta de EF sin materializar, la consulta se ejecuta
// al serializar la respuesta y la excepción llega hasta acá.
public class DatabaseUnavailableMiddleware
{
    private readonly RequestDelegate _next;

    public DatabaseUnavailableMiddleware(RequestDelegate next)
    {
        _next = next;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await _next(context);
        }
        catch (Exception e) when (DatabaseUnavailable.Matches(e) && !context.Response.HasStarted)
        {
            context.Response.Clear();
            context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
            await context.Response.WriteAsJsonAsync(new { Message = DatabaseUnavailable.Message });
        }
    }
}
