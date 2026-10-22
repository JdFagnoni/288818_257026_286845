# Rate limiting en el API Gateway

El gateway limita cuántos requests puede hacer cada cliente, para que un cliente que manda demasiado tráfico (por error o a propósito) no deje sin servicio al resto. Cuando un cliente se pasa del límite, el gateway responde `429 Too Many Requests` sin reenviar el request a los microservicios.

El modo se elige con `RateLimiting:Mode` en `appsettings.json`. Hay tres: `IP`, `User` y `Disabled`.

## Qué modo está activo

| Entorno | Archivo | Modo |
| --- | --- | --- |
| Kubernetes y docker-compose | `appsettings.json` | `IP` |
| Desarrollo local (`dotnet run`) | `appsettings.Development.json` | `Disabled` |

## Modo `IP` (el que se usa en el cluster)

Usa la librería AspNetCoreRateLimit. Cada IP tiene su propio contador y las reglas están en `IpRateLimiting.GeneralRules`:

| Endpoint | Límite |
| --- | --- |
| `POST /api/login` | 10 por minuto (frena intentos de adivinar contraseñas) |
| Cualquier otro | 100 por minuto y 1000 por hora |

`/health`, `/metrics` y los `OPTIONS` de CORS no cuentan, para que las probes de Kubernetes y Prometheus nunca queden bloqueadas.

### De dónde sale la IP

Se usa la IP de la conexión TCP y nada más (`ConnectionIpRateLimitConfiguration`).

Antes el gateway también leía el header `X-Real-IP`. Ese header lo manda el cliente, así que alcanzaba con cambiarlo en cada request para que cada uno contara como una IP nueva y el límite no se aplicara nunca. Solo tiene sentido confiar en ese header cuando lo agrega un proxy propio delante del gateway, y acá no hay ninguno: el navegador le habla directo al gateway. Ojo con un detalle de la librería: lee `X-Real-IP` por defecto aunque se borre `RealIpHeader` de la configuración. Por eso se reemplazan sus resolvers en vez de tocar solo el JSON.

### Límites a tener en cuenta

- **Cada réplica del gateway cuenta por separado.** Los contadores están en la memoria de cada pod. Con 2 réplicas, el tráfico se reparte entre las dos y el límite real queda en unos 200 por minuto en vez de 100. Para un límite exacto habría que compartir los contadores, por ejemplo con Redis.
- **Varios usuarios pueden compartir una IP.** Por ejemplo, una oficina detrás de un mismo router. En este cluster pasa siempre: todo lo que entra con `kubectl port-forward` llega al gateway como `127.0.0.1`, y por NodePort kube-proxy suele reemplazar la IP de origen por la del nodo. En la demo, todos los clientes comparten un solo límite. Para tener la IP real del cliente haría falta un ingress delante que agregue `X-Forwarded-For`, y que el gateway confíe en ese header solo cuando viene de ese ingress.

## Modo `User`

Usa un middleware propio (`UserRateLimitMiddleware`). En vez de la IP, identifica al cliente así, en este orden:

1. Los primeros 8 caracteres del header `Authorization`.
2. El header `X-User-Id`.
3. Si no viene ninguno de los dos, la IP de la conexión.

Los límites son 100 por minuto y 1000 por hora, y están fijos en el código (`MaxRequestsPerMinute` y `MaxRequestsPerHour`). Las claves con esos nombres en `appsettings.json` no se leen.

Este modo no está activo, y no conviene activarlo tal como está: el token no se valida, así que un cliente puede mandar un `Authorization` o un `X-User-Id` distinto en cada request y nunca llegar al límite. Es el mismo problema que tenía `X-Real-IP`. Para usarlo habría que identificar al usuario a partir de un token validado.

## Modo `Disabled`

No aplica ningún límite. Es el que usa `appsettings.Development.json` para trabajar en local sin que las pruebas manuales choquen con el 429.

## Cómo cambiar los límites

Se editan las reglas de `IpRateLimiting.GeneralRules` en `appsettings.json` y se reconstruye la imagen del gateway. Por ejemplo, para dejar `GET /api/drug` en 200 por minuto:

```json
{
  "Endpoint": "GET:/api/drug",
  "Period": "1m",
  "Limit": 200
}
```

Si se quiere que una red interna no tenga límite, se agrega en `IpRateLimitPolicies.IpRules` con `"Limit": 0`.

## Cómo probarlo

Desde dentro del cluster, con un pod temporal que manda 250 requests seguidos:

```bash
kubectl run rl-check -n pharmago --rm -i --restart=Never --image=busybox:1.36 -- sh -c '
  for i in $(seq 1 250); do
    wget -S -q -O /dev/null http://pharmago-api-gateway/test-health 2>&1 | grep -o "HTTP/1.1 [0-9]*"
  done | sort | uniq -c'
```

Con 2 réplicas del gateway, el resultado esperado es alrededor de 200 respuestas `200` y 50 `429`. Si se repite agregando `--header "X-Real-IP: 10.0.0.$i"` al `wget`, el resultado tiene que ser el mismo: así se comprueba que cambiar ese header ya no evade el límite.
