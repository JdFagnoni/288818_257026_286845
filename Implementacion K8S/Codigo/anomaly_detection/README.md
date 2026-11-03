# Detección de anomalías

Implementación del ítem 5 del obligatorio: `IsolationForest` y `OneClassSVM(kernel="rbf")` sobre datasets tabulares. El [notebook](deteccion_anomalias.ipynb) muestra ambos modelos, sus parámetros, una comparación de las anomalías detectadas y una gráfica con los puntos marcados. El CLI usa el mismo pipeline para repetir el análisis sin editar el notebook.

Este componente corre fuera de Kubernetes. Lee un CSV o consulta Prometheus mediante su API; no crea pods ni cambia la ubicación de los servicios. `examples/prometheus_sample.csv` y `examples/checkout_sintetico_docente.csv` sirven como entradas reproducibles; `export-prometheus` permite generar otra muestra del clúster.

## Preparación

Se probó con Python 3.13. Desde `Implementacion K8S/Codigo/anomaly_detection`:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Para correr el notebook, ejecutar `jupyter lab`, abrir `deteccion_anomalias.ipynb` y seleccionar el intérprete `.venv/bin/python`. También se puede abrir en VS Code con ese intérprete. El notebook parte del CSV de ejemplo y no necesita conexión al clúster.

## Probar la muestra de Prometheus

```bash
python cli.py analyze \
  --input examples/prometheus_sample.csv \
  --timestamp-column timestamp \
  --plot-feature cpu_percent \
  --output-dir output/prometheus
```

Genera `results.csv` con una bandera `*_anomaly` (1 = anomalía) y un `*_score` por modelo, `summary.json` con parámetros/conteos/acuerdo y `comparison.png` con los puntos marcados. Los scores están invertidos respecto a `decision_function`: **mayor score = más anómalo** dentro de cada modelo; sus escalas no se comparan entre sí.

Para actualizar la muestra con métricas reales, iniciar `./port-forward.sh` desde `../k8s` en otra terminal (o `kubectl port-forward -n pharmago svc/prometheus 9090:9090`) y ejecutar:

```bash
python cli.py export-prometheus \
  --url http://127.0.0.1:9090 \
  --node pharmago-m02 \
  --hours 6 --step 60 \
  --output data/prometheus_m02.csv
python cli.py analyze --input data/prometheus_m02.csv \
  --timestamp-column timestamp --output-dir output/prometheus_m02
```

El exportador consulta CPU, memoria y disco del nodo, además de tráfico y errores de red. Si alguna serie no existe en ese rango, la omite; el análisis excluye métricas vacías o constantes. Las series se alinean por instante y se guardan con fecha UTC.

## Usar un dataset docente

### Dataset sintético del notebook de clase

El notebook compartido por el docente **genera** un dataset de 300 ventanas de un servicio de checkout; no trae un CSV externo ni modelos ya entrenados. Las celdas de los detectores están marcadas como `TODO`. Reprodujimos el generador con semilla 42 en [generate_checkout_sintetico.py](examples/generate_checkout_sintetico.py) y versionamos el [CSV resultante](examples/checkout_sintetico_docente.csv). Cada fila contiene `requests`, `error_rate`, `latency_ms` y `cpu_pct`. `kind` e `is_anomaly` son etiquetas para evaluar: no deben entrar en el entrenamiento.

Para regenerar el CSV y ejecutar los dos modelos requeridos con los parámetros indicados por el notebook (`n_estimators=300`, `contamination=0.10`, `nu=0.10`, `gamma=scale`, semilla 42):

```bash
python examples/generate_checkout_sintetico.py
python cli.py analyze \
  --input examples/checkout_sintetico_docente.csv \
  --timestamp-column timestamp \
  --features requests error_rate latency_ms cpu_pct \
  --label-column is_anomaly --anomaly-label 1 \
  --contamination 0.10 --nu 0.10 --gamma scale \
  --n-estimators 300 --random-state 42 \
  --plot-feature error_rate \
  --output-dir output/checkout_sintetico_docente
```

Los dos modelos se ajustan con las 300 ventanas escaladas y sin etiquetas. Isolation Forest usa su valor por defecto `max_samples="auto"`, que en este caso toma 256 ventanas por árbol, como pide el notebook. Las columnas de etiquetas se conservan en `results.csv` para comparar las predicciones. Con esta versión del CSV, hay 53 ventanas etiquetadas como anómalas:

| Modelo | Alertas | Precisión | Recall | F1 |
|---|---:|---:|---:|---:|
| Isolation Forest | 30 | 0.800 | 0.453 | 0.578 |
| One-Class SVM (RBF) | 29 | 0.621 | 0.340 | 0.439 |

Por ejemplo, Isolation Forest detecta las 8 ventanas de `incidente_5xx` y las 4 de `timeouts`, pero ninguna de las 18 de `degradacion`; también marca 4 de las 20 ventanas `batch` que el docente considera legítimas. El SVM marca 4 de 8 de `incidente_5xx`, 1 de 18 de `degradacion` y 5 de 20 de `batch`. Son métricas exploratorias **in-sample**: entrenamiento y evaluación usan las mismas ventanas contaminadas, tal como establece el ejercicio. El notebook también pide comparar k-sigma y MAD, pero esos métodos no forman parte del requisito de implementar Isolation Forest y SVM con RBF.

### CSV externos

Colocar el CSV en `data/` (ignorado por Git) y cambiar la ruta en el notebook o ejecutar, por ejemplo:

```bash
python cli.py analyze \
  --input data/dataset_docente.csv \
  --timestamp-column fecha \
  --label-column es_anomalia --anomaly-label 1 \
  --features cpu memoria disco red \
  --contamination 0.05 --nu 0.05 --gamma scale \
  --plot-feature cpu \
  --output-dir output/docente
```

Los nombres de columnas son ilustrativos: usar los reales del CSV. Si el archivo usa separador `;` y coma decimal, agregar `--delimiter ';' --decimal ','`. Si no hay fecha, etiqueta o columnas conocidas, omitir esos argumentos: se tomarán las columnas numéricas y se graficará por número de fila. `label`, `target`, `class`, `anomaly`, `is_anomaly`, `id` e `index` se excluyen de la selección automática; indicar `--label-column` para excluir otro nombre de etiqueta. Las columnas categóricas requieren preparación explícita antes de entrenar.

El pipeline convierte las métricas a números, reemplaza valores infinitos por faltantes, descarta columnas vacías o constantes, imputa las demás por mediana y estandariza antes de ajustar ambos modelos. Usa la misma matriz para los dos y una semilla fija. Por defecto ajusta con hasta 5000 filas elegidas de forma reproducible y puntúa **todas** las filas. `--max-train-rows` permite cambiar el límite; es especialmente útil porque el SVM con RBF puede requerir mucho tiempo/memoria en datasets grandes.

`contamination` determina el umbral de Isolation Forest; `nu` y `gamma` controlan One-Class SVM. No fuerzan el mismo porcentaje de anomalías: comparar conteos, intersección y las filas detectadas, no solo un número final. Si se proporcionan etiquetas, el resumen agrega precisión, recall y F1, pero son métricas **in-sample** porque el pipeline ajusta y puntúa el mismo dataset; no sustituyen una evaluación con un conjunto independiente. Conviene ajustar `nu` y `gamma` cuando se conozca la distribución del dataset docente.

## Verificación

```bash
python -m unittest discover -s tests -v
python cli.py analyze --input examples/prometheus_sample.csv --output-dir output/smoke
```

Los directorios `data/` y `output/` quedan fuera de Git. Los ejemplos de `examples/` y el notebook del proyecto sí están versionados para que el flujo se pueda reproducir sin acceder al Prometheus local.
