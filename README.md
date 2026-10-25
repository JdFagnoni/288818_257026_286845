# AIOps

[![CI/CD](https://github.com/JdFagnoni/288818_257026_286845/actions/workflows/ci-cd.yml/badge.svg?branch=main)](https://github.com/JdFagnoni/288818_257026_286845/actions/workflows/ci-cd.yml)

## CI/CD

El pipeline está definido en [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml) y corre en GitHub Actions.

| Job | Cuándo corre | Qué hace |
|---|---|---|
| Backend | En cada pull request a `main` y en cada push a `main` | Compila la solución .NET y corre los tests de `PharmaGo.Test`. Los resultados quedan como artefacto del run. |
| Frontend | Ídem | Instala las dependencias con `npm ci` y compila el frontend Angular. |
| Detección de anomalías | Ídem | Prueba Isolation Forest y One-Class SVM RBF con datos sintéticos, ejecuta el análisis de muestra y el notebook. |
| Kubernetes manifests | Ídem | Valida los manifiestos de `Implementacion K8S/Codigo/k8s` contra el esquema de Kubernetes con `kubeconform`. |
| Image | Ídem | Construye las cuatro imágenes Docker, sin publicarlas. |
| Publish | Solo en `main`, y solo si todos los jobs anteriores pasaron | Publica las imágenes en GitHub Container Registry. |

Las imágenes publicadas son `pharmago-api-gateway`, `pharmago-users-service`, `pharmago-pharmacy-service` y `pharmago-ui`, en `ghcr.io/jdfagnoni/<imagen>`, con dos tags: `sha-<commit>`, que identifica exactamente qué versión de `main` contiene, y `latest`.

El análisis del ítem 5 está en [anomaly_detection](Implementacion%20K8S/Codigo/anomaly_detection/README.md). Incluye el notebook, un CLI para datasets CSV y métricas de Prometheus, y pruebas automatizadas. Los datasets docentes se incorporarán cuando estén disponibles.

Para que un pull request no se pueda mergear con el pipeline en rojo, en *Settings → Branches* se agrega una regla para `main` con *Require status checks to pass before merging*, eligiendo los jobs del pipeline.
