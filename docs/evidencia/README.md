# Evidencia del obligatorio

Capturas y salidas de terminal que respaldan cada ítem de la rúbrica. Cada carpeta corresponde a un ítem y a su issue en el [tablero](https://github.com/users/JdFagnoni/projects/1).

| Carpeta | Contenido | Issue |
|---|---|---|
| `00-linea-base/` | Estado del sistema antes de los cambios del obligatorio | [#12](https://github.com/JdFagnoni/288818_257026_286845/issues/12) |
| `01-plataforma/` | Ítem 1. Implementación de plataforma | [#3](https://github.com/JdFagnoni/288818_257026_286845/issues/3) |
| `02-alta-disponibilidad/` | Ítem 2. Alta disponibilidad | [#4](https://github.com/JdFagnoni/288818_257026_286845/issues/4) |
| `03-despliegues-seguros/` | Ítem 3. Despliegues seguros | [#5](https://github.com/JdFagnoni/288818_257026_286845/issues/5) |
| `04-telemetria/` | Ítem 4. Telemetría | [#6](https://github.com/JdFagnoni/288818_257026_286845/issues/6) |
| `05-deteccion-anomalias/` | Ítem 5. Detección de anomalías | [#7](https://github.com/JdFagnoni/288818_257026_286845/issues/7) |
| `06-plan-contencion/` | Ítem 6. Plan de contención de incidentes | [#8](https://github.com/JdFagnoni/288818_257026_286845/issues/8) |
| `07-scripts-caos/` | Ítem 7. Scripts de caos | [#9](https://github.com/JdFagnoni/288818_257026_286845/issues/9) |
| `08-defensa/` | Ítem 8. Defensa (war-room) | [#10](https://github.com/JdFagnoni/288818_257026_286845/issues/10) |

## Convenciones

- **Nombre:** `NN-descripcion.ext`. `NN` es el orden dentro de la carpeta (`01`, `02`, …) y la descripción va en minúsculas con guiones. Ejemplo: `02-grafana-overview.png`.
- **Formato:** capturas de pantalla en PNG; salidas de terminal en `.txt`, copiadas tal cual y con el comando al principio.
- **Índice:** cada archivo nuevo se agrega a la tabla de su carpeta, más abajo, con qué muestra y la fecha.
- **Sin datos sensibles:** nada de tokens, contraseñas personales ni datos de terceros.

## Índice

### 00 · Línea base

| Archivo | Qué muestra | Fecha |
|---|---|---|
| [01-nodos-y-pods.png](00-linea-base/01-nodos-y-pods.png) | Los 4 nodos con su rol (`node-type`: 1 `ops` y 3 `backend`) y todos los pods del namespace `pharmago`, con el nodo donde corre cada uno. Cada microservicio tiene sus 2 réplicas en un nodo propio. | 2026-10-21 |
