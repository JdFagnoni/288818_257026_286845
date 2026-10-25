#!/usr/bin/env bash
# Reconstruye las imágenes con logs y trazas instrumentados en Minikube.
set -euo pipefail

cd "$(dirname "$0")/../Backend"

for service in UsersService PharmacyService ApiGateway; do
  case "$service" in
    UsersService) image=pharmago-users-service ;;
    PharmacyService) image=pharmago-pharmacy-service ;;
    ApiGateway) image=pharmago-api-gateway ;;
  esac
  docker build -f "PharmaGo.$service/Dockerfile" -t "$image:latest" .
  minikube -p pharmago image load --overwrite=true "$image:latest"
done

cd ../Frontend
docker build -f Dockerfile -t pharmago-ui:latest .
minikube -p pharmago image load --overwrite=true pharmago-ui:latest
