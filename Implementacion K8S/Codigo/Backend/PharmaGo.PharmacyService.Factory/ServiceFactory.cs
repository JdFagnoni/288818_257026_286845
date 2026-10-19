using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using PharmaGo.PharmacyService.BusinessLogic;
using PharmaGo.DataAccess;
using PharmaGo.DataAccess.Repositories;
using PharmaGo.Domain.Entities;
using PharmaGo.PharmacyService.IBusinessLogic;
using PharmaGo.IDataAccess;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using InstrumentationInterface;
using Instrumentation;


namespace PharmaGo.PharmacyService.Factory
{
    public static class ServiceFactory
    {

        public static void RegisterBusinessLogicServices(this IServiceCollection serviceCollection, IConfiguration configuration)
        {
            serviceCollection.AddScoped<IStockRequestManager, StockRequestManager>();
            serviceCollection.AddScoped<IPurchasesManager, PurchasesManager>();
            serviceCollection.AddScoped<IPharmacyManager, PharmacyManager>();
            serviceCollection.AddScoped<IDrugManager, DrugManager>();
            serviceCollection.AddScoped<IPresentationManager, PresentationManager>();
            serviceCollection.AddScoped<IUnitMeasureManager, UnitMeasureManager>();
            serviceCollection.AddScoped<IExportManager, ExportManager>();
            
            serviceCollection.AddHttpClient<PharmaGo.PharmacyService.HttpClients.UsersServiceClient>(client =>
            {
                var serviceUrl = configuration["ServiceUrls:UsersService"] ?? "http://127.0.0.1:5001";
                client.BaseAddress = new Uri(serviceUrl);
                client.Timeout = TimeSpan.FromSeconds(30);
            });
        }

        public static void RegisterDataAccessServices(this IServiceCollection serviceCollection, IConfiguration configuration)
        {
            serviceCollection.AddScoped<IRepository<User>, UsersRepository>();
            serviceCollection.AddScoped<IRepository<Session>, SessionRepository>();
            serviceCollection.AddScoped<IRepository<StockRequest>, StockRequestRepository>();
            serviceCollection.AddScoped<IRepository<Pharmacy>, PharmacyRepository>();
            serviceCollection.AddScoped<IRepository<UnitMeasure>, UnitMeasureRepository>();
            serviceCollection.AddScoped<IRepository<Purchase>, PurchasesRepository>();
            serviceCollection.AddScoped<IRepository<Presentation>, PresentationRepository>();
            serviceCollection.AddScoped<IRepository<Drug>, DrugRepository>();
            serviceCollection.AddScoped<IRepository<PurchaseDetail>, PurchasesDetailRepository>();

            serviceCollection.AddDbContext<DbContext, PharmacyGoDbContext>(options =>
            {
                options.UseSqlServer(
                    configuration.GetConnectionString("DefaultConnection"),
                    sqlOptions => { sqlOptions.MigrationsAssembly("PharmaGo.DataAccess"); });
                options.ConfigureWarnings(w =>
                {
                    w.Ignore(RelationalEventId.CommandExecuted);
                    w.Ignore(RelationalEventId.CommandError);
                });
            });
            serviceCollection.AddSingleton<ICustomMetrics, CustomMetrics>();
            serviceCollection.AddSingleton<IStructuredLogger, StructuredLogger>();
        }

        // Construye el modelo de EF Core y abre la primera conexión del pool antes de
        // que la app escuche: así la startup probe deja al pod fuera del Service hasta
        // que responde con latencia normal. Sin esto, la primera consulta de cada pod
        // nuevo tarda ~3s.
        public static IHost WarmUpDatabase(this IHost host)
        {
            using (var scope = host.Services.CreateScope())
            {
                try
                {
                    var dbContext = scope.ServiceProvider.GetRequiredService<DbContext>();
                    dbContext.Set<Drug>().AsNoTracking().Any();
                }
                catch (Exception ex)
                {
                    // Arranca igual: los requests que usan la BD fallan hasta que esté
                    // disponible, como pasaba antes de agregar el warm-up.
                    var logger = scope.ServiceProvider.GetRequiredService<ILoggerFactory>().CreateLogger(nameof(WarmUpDatabase));
                    logger.LogWarning(ex, "Database warm-up failed");
                }
            }

            return host;
        }

    }
}

