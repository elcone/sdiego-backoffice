# Preparación de producción — Privada San Diego

Estado: **preparado localmente; no instalado ni aplicado**.

Preflight final y comandos del primer despliegue: [FIRST_DEPLOYMENT.md](FIRST_DEPLOYMENT.md). Los bloques de instalación
son instrucciones para una ejecución futura y autorizada. No son un script automático.

## Bloqueos antes de instalar o publicar

- Dominio confirmado: `privadasandiego.elcone.dev`. DNS todavía pendiente: crear
  A `privadasandiego` hacia `160.153.179.18`; no crear AAAA sin IPv6 verificada.
- Decisión definitiva: sin limitación de login basada en IP. No se añade caché,
  middleware, Redis, módulos Caddy ni umbrales para ese fin.
- Operación inicial sin correo: configurar explícitamente
  `EMAIL_BACKEND=django.core.mail.backends.dummy.EmailBackend`. No se requieren
  variables SMTP. Los mensajes se descartan; la recuperación por correo de Viewflow
  no entregará mensajes hasta configurar SMTP real. Login, sesiones, logout y
  operaciones administrativas no dependen de recibir correo.
- Aprobar el valor inicial de HSTS, sin includeSubDomains ni preload.
- Ejecutar `core.0003_initial_fee_configuration` normalmente: sus dos
  configuraciones están aceptadas; no omitir, modificar ni revertir la migración. `core.0004` también exige que la tabla bancaria antigua
  esté vacía. Una restauración debe conservar el historial real de migraciones.
- Primer despliegue aprobado: sin casas, cargos, movimientos, usuarios ni historial.
  Administrador creado posteriormente por el propietario; CSV en otra etapa.
  Conservar las dos configuraciones aceptadas de core.0003. Confirmar destinatarios
  age, ubicación externa, retención, RPO, RTO y responsables. No ejecutar importadores.
- Obtener evidencia de una restauración íntegra y repetir el preflight inmediato.
- Usar el commit productivo `chore: prepare production release` y el frontend
  `641b6446cf9ce9ff6592d954286bd189158816e9`, según FIRST_DEPLOYMENT.md y el reporte
  de entrega. El backend base anterior no contiene esta preparación.

## Verificación incremental realizada

SSH de solo lectura a `datamanager@rdbhs.elcone.dev`, noviembre de 2026:

- 4 vCPU; aproximadamente 13 GiB de RAM disponibles; 173 GiB libres en `/`.
- Caddy 2.11.4, PostgreSQL 17.11, RDBHS y Superset: activos.
- RDBHS: `127.0.0.1:8001`; Superset: `127.0.0.1:8088`;
  PostgreSQL: `127.0.0.1:5432`; Caddy: 80/443, administración en loopback 2019.
- 8002 libre. Sin usuario `sdiego-backoffice`, directorios o unidad propuestos.
  Sin base `sdiego_backoffice` ni roles `sdiego_app`/`sdiego_migrator`.
- PostgreSQL utiliza SCRAM-SHA-256 para las reglas TCP loopback inspeccionadas.
- UFW está activo y contiene una regla permisiva heredada para 5432; PostgreSQL
  escucha solo en loopback. No se cambió esa regla ni el listener; no ampliar
  listen_addresses. Cualquier ajuste del firewall requiere revisión independiente.
- Sitios Caddy: `backoffice.elcone.dev` y `superset.elcone.dev`, con sus rutas
  existentes. SHA256 del Caddyfile inspeccionado:
  `49deeb3cc893fd64e18721fb312ac1080fc8681482e3b13e33dab75e09fd8aac`.
- HTTPS de `/accounts/login/` de RDBHS y `/health` de Superset: 200.
- Hay dumps históricos de octubre en `/var/backups/rdbhs-backoffice/` y
  `/var/backups/rdbhs-superset/`. Hay copias age históricas fuera del VPS en
  la estación de recuperación. Esto **no prueba restauración ni vigencia**.
- No se confirmó un timer dedicado de respaldo, restauraciones verificadas ni
  el estado actual de snapshots GoDaddy. No se inspeccionaron secretos.

Repetir solo estas lecturas antes de instalar. Si cambiaron el Caddyfile, servicios,
puertos o respaldos, revisar la diferencia; no restaurar una configuración antigua.

## Arquitectura y permisos

Reutilizar Caddy, PostgreSQL y systemd existentes. No instalar Node/npm/Vite en el
VPS ni usar `vite preview`. No abrir 8002 ni 5432 al exterior.

```text
/srv/sdiego-backoffice/                       root:root 0755
  releases/<BACKEND_SHA>-<FRONTEND_SHA>/      root:root 0755
    backend/                                root:sdiego-backoffice 0750
      .venv/                                root:sdiego-backoffice, solo lectura runtime
    frontend/                               root:root, directorios 0755/archivos 0644
    static/                                 root:root, directorios 0755/archivos 0644
  current -> releases/<par aprobado>         un solo enlace para cambiar ambos artefactos
  shared/                                   sdiego-backoffice:sdiego-backoffice 0700
    private-media/                          sdiego-backoffice:sdiego-backoffice 0700
/etc/sdiego-backoffice/                      root:root 0700
  backend.env                               root:root 0600
  migration.env                             root:root 0600, separado del servicio
```

Caddy solo necesita atravesar las rutas públicas y leer `frontend/` y `static/`.
Nunca darle acceso al entorno, código privado, copias o medios privados. El usuario
runtime no debe modificar código, entorno ni el enlace `current`.

Servicio: `sdiego-backoffice.service`. Listener: `127.0.0.1:8002`, dos workers
iniciales; confirmar consumo en staging. No reutilizar usuarios, entornos, bases,
virtualenvs ni unidades de RDBHS/Superset.

## Variables

`backend.env.example` solo contiene marcadores. Nunca copiarlo como credenciales.
Crear el archivo real mediante el mecanismo protegido del operador. No imprimirlo,
no usar `set -x`, ni secretos como argumentos o dentro del historial de shell.

- `DJANGO_SETTINGS_MODULE=sdiego_backoffice.settings_production`.
- `SECRET_KEY`: obligatoria, aleatoria fuerte, persistente e independiente de RDBHS;
  conservarla en reinicios y rollback, compartida entre todos los workers.
- `DATABASE_URL`: PostgreSQL local, base `sdiego_backoffice`, usuario `sdiego_app`.
- `ALLOWED_HOSTS`: dominio confirmado, sin esquema, rutas ni comodines.
  Los orígenes CSRF HTTPS se derivan de esta lista. No se necesita CORS.
- `SECURE_HSTS_SECONDS`: valor explícitamente aprobado.
- `STATIC_ROOT`: ruta absoluta `.../releases/<par>/static` del release correspondiente.
- `MEDIA_ROOT=/srv/sdiego-backoffice/shared/private-media`.
- `EMAIL_BACKEND`: dummy explícito para esta etapa; envío deshabilitado temporalmente.
  SMTP sigue siendo el valor predeterminado si no se define esta variable y exige
  `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` y
  `DEFAULT_FROM_EMAIL` reales y no vacíos. No reutilizar secretos de otros proyectos.
  `check --deploy` identifica dummy como mail.E001; no se silencia ese check.

El módulo productivo exige estas variables del proceso, ignora el `.env` local,
impone DEBUG=False, cookies Secure, redirección HTTPS y confianza del esquema
solo detrás del listener loopback. Caddy sobrescribe `X-Forwarded-Proto`.
Los logs de Django omiten mensajes, trazas, cuerpos y objetos request; registran
nivel, logger y estado. Gunicorn no registra URL, query ni cabeceras; el log de acceso del sitio Caddy elimina
URI y cabeceras. Los logs runtime globales de Caddy requieren comprobación adicional
sin modificar las políticas de los sitios existentes. Esto limita detalle diagnóstico deliberadamente.
Revisar también la política/retención del journal existente, sin cambiar la de otros servicios.

## Construcción y transferencia local del frontend

Desde la estación local, reemplazando el SHA por la revisión aprobada:

```bash
./deploy/build_frontend.sh /ruta/al/sdiego-backoffice-frontend \
  641b6446cf9ce9ff6592d954286bd189158816e9 /tmp/sdiego-artifacts
```

El script archiva solo el commit exacto (no el árbol de trabajo), ejecuta `npm ci`,
pruebas, build, verifica index/assets, rechaza enlaces, archivos ocultos, sourcemaps
inesperados y archivos de backend, y genera un tar y SHA256. No modifica el repo
frontend. La reproducción fija las dependencias; registrar también Node/npm de
construcción (validados localmente Node 24.16.0, npm 11.13.0).

Transferencia futura, a un staging de propiedad del operador **confirmado antes**:

```bash
rsync -av -- /tmp/sdiego-artifacts/641b6446cf9ce9ff6592d954286bd189158816e9.tar.gz \
  /tmp/sdiego-artifacts/641b6446cf9ce9ff6592d954286bd189158816e9.tar.gz.sha256 \
  datamanager@rdbhs.elcone.dev:<STAGING_CONFIRMADO>/
```

No usar `rsync --delete` ni sincronizar el árbol local completo. En destino,
`sha256sum -c <SHA>.tar.gz.sha256` desde staging antes de extraer. Guardar el SHA
del commit y del artefacto en el manifiesto privado del release.

## Orden exacto de instalación futura

No ejecutar mientras haya bloqueos ni sin autorización de despliegue.

1. Repetir preflight, comprobar 8002, nombres incluidos `sdiego_owner`, recursos,
   estado HTTP de RDBHS/Superset y hash del Caddyfile. Verificar UFW sin modificarlo.
2. Respaldar la configuración Caddy y los artefactos/metadatos relevantes mediante
   el procedimiento aprobado; confirmar restauración y copia externa antes de tocar
   configuración compartida. No producir un snapshot nuevo sin autorización.
3. En local, archivar el backend desde un SHA completo revisado:
   `git archive --format=tar.gz --output=<BACKEND_SHA>.tar.gz <BACKEND_SHA>`.
   Generar checksum, transferir por rsync/SSH y verificar en destino. No incluir
   `.env`, recursos privados, mocks ajenos ni archivos sin seguimiento.
4. Crear únicamente el usuario y directorios independientes:

   ```bash
   sudo useradd --system --user-group --home-dir /srv/sdiego-backoffice --no-create-home --shell /usr/sbin/nologin sdiego-backoffice
   sudo install -d -o root -g root -m 0755 /srv/sdiego-backoffice /srv/sdiego-backoffice/releases
   sudo install -d -o root -g root -m 0700 /etc/sdiego-backoffice
   sudo install -d -o sdiego-backoffice -g sdiego-backoffice -m 0700 /srv/sdiego-backoffice/shared /srv/sdiego-backoffice/shared/private-media
   ```

5. Definir un `release` absoluto cuyo nombre incluya ambos SHAs completos, comprobar
   que no existe y crear `backend`, `frontend` y `static`. Extraer los dos archivos
   verificados en sus directorios correspondientes. Asignar los permisos descritos.
6. Preparar el entorno Python **solo de San Diego**, usando el uv existente y
   caché propia; no actualizar paquetes del sistema ni entornos de otras aplicaciones:

   ```bash
   # Desde "$release/backend", como operador con privilegios de instalación:
   sudo env UV_CACHE_DIR=/srv/sdiego-backoffice/uv-cache \
     /home/datamanager/.local/bin/uv sync --locked --no-dev --extra production --python /usr/bin/python3
   ```

   Confirmar antes que el uv/Python existentes siguen disponibles. Proteger el
   backend y virtualenv con propietario root, grupo sdiego-backoffice y sin permisos
   de escritura del servicio. No ejecutar npm, node o vite en el VPS.
7. Revisar y ejecutar explícitamente `deploy/postgresql.sql` como administrador:
   `sudo cat <SQL_REVISADO> | sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres`.
   El script falla ante nombres existentes; no reutiliza ni elimina recursos.
   Establecer contraseñas interactivamente con `\password sdiego_app` y
   `\password sdiego_migrator`. No modificar pg_hba.conf ni otras bases sin
   una incompatibilidad concreta y autorización específica.
8. Crear los entornos protegidos. `migration.env` es independiente y usa
   `sdiego_migrator`; el módulo configura `SET role=sdiego_owner` al conectar.
   No conceder membresía de owner al rol runtime. El usuario Linux runtime no puede
   leer los archivos protegidos; systemd los carga como administrador.
9. Ejecutar comandos de gestión del release como unidades transitorias, con el
   entorno apropiado y sin imprimirlo. Patrón:

   ```bash
   sudo systemd-run --quiet --wait --pipe --collect \
     --uid=sdiego-backoffice \
     --property=EnvironmentFile=/etc/sdiego-backoffice/migration.env \
     --working-directory="$release/backend" \
     "$release/backend/.venv/bin/python" manage.py migrate --plan
   ```

   Revisar el plan completo del destino. En una base nueva habrá migraciones de
   Django, core y treasury; no asumir el estado de la base local. Respaldar antes,
   y solo entonces ejecutar `migrate --noinput` con el mismo patrón. Los permisos
   predeterminados del owner conceden DML al rol runtime. Verificar que `sdiego_app`
   no puede crear/alterar tablas ni asumir el owner. No ejecutar pruebas Django
   contra la base real ni comandos de importación histórica.
10. Con el entorno runtime, ejecutar `check`, `check --deploy`, `migrate --check` y
    comprobación de conexión/rol. Para `collectstatic --noinput`, usar una unidad
    de instalación root con el entorno protegido y el STATIC_ROOT del nuevo release;
    el runtime no necesita escribir allí. Dejar estáticos públicos root-owned y legibles
    por Caddy. No publicar si falla un check o no se resolvió el estado inicial.
11. Registrar el destino previo de `current` y la configuración protegida asociada.
    Cambiar el par completo mediante enlace nuevo en el mismo filesystem:

    ```bash
    sudo ln -s "$release" /srv/sdiego-backoffice/current.next
    sudo mv -Tf /srv/sdiego-backoffice/current.next /srv/sdiego-backoffice/current
    ```

    Parar si existe `current.next` inesperadamente. En actualizaciones con escrituras,
    usar ventana de mantenimiento exclusiva de San Diego antes del cambio. Nunca
    mantener simultáneamente dos sistemas aceptando operaciones financieras.
12. Instalar **solo** `sdiego-backoffice.service`. `systemctl daemon-reload` y activar
    solo esa unidad en la ventana autorizada. Comprobar listener `127.0.0.1:8002`,
    usuario, límites, entorno productivo y salud local con Host del dominio y cabecera
    HTTPS. No reiniciar PostgreSQL, RDBHS, Superset ni Caddy.
13. Revisar `Caddyfile.fragment`, ya fijado a `privadasandiego.elcone.dev`;
    no añadir variables globales a la unidad Caddy.
    Mantener byte a byte los bloques existentes. Revisar el diff contra el Caddyfile
    vigente (su hash puede haber cambiado). Validar la copia completa con
    `sudo caddy validate --config <COPIA_PROPUESTA> --adapter caddyfile`.
14. Solo con autorización y DNS confirmado, incorporar el bloque nuevo y recargar
    Caddy de forma controlada, sin reiniciarlo. Verificar de inmediato RDBHS y
    Superset. Si hace falta cambiar DNS, es una acción independiente pendiente.
15. Verificar certificado, rutas Vue con recarga directa, estáticos, autenticación,
    cookies e integridad. Habilitar operaciones solo tras
    aprobación de los resultados. Registrar revisiones, hashes y evidencia.

## Datos, respaldo y restauración

Para una base ya existente, pausar escrituras de San Diego antes del backup final.
El primer despliegue usa una base nueva sin transacciones ni usuarios. No restaurar
una base con datos, crear usuarios ni ejecutar importadores. core.0003 inserta dos
configuraciones de cuotas expresamente aceptadas; se conservan sin modificaciones.

- Capturar conteos y agregados con transacción READ ONLY REPEATABLE READ: cargos
  por casa/tipo/periodo, movimientos por cuenta/tipo, importes exactos Decimal,
  últimos saldos bancarios por timestamp/id, asignaciones y estados de revisión.
- Comparar estados de cuenta, saldos por casa y resumen con el mismo corte temporal.
  Mantener `core` y `treasury` separados, respetando signos, créditos y reglas vigentes.
- Respaldar base con `pg_dump -Fc`, no una copia de PGDATA en caliente. Cifrar con
  age mediante pipe, sin un dump plaintext en directorios compartidos:

  ```bash
  # Solo en shell root autorizado; destinatario age público previamente validado.
  # BACKUP_FILE debe ser una ruta nueva dentro de un directorio root-only.
  set -euo pipefail
  umask 077
  sudo -u postgres pg_dump -Fc sdiego_backoffice | age -r "$AGE_RECIPIENT" -o "$BACKUP_FILE"
  sha256sum "$BACKUP_FILE" > "$BACKUP_FILE.sha256"
  ```

- Conservar además, cifrados y con permisos 0600, medios privados, entornos,
  configuración y manifiesto de release. Nunca imprimir sus contenidos.
- Copiar el cifrado y checksum fuera del VPS por SSH, verificar el checksum allí.
  Los snapshots GoDaddy son complementarios; comprobar su fecha y capacidad real
  de recuperación, sin crearlos ni restaurarlos en esta preparación.
- Restaurar en una base aislada de nombre y credenciales distintos, preferiblemente
  fuera del VPS compartido. Con `PGSERVICE=sdiego_restore` previamente definido en
  un archivo protegido que apunte **solo** a esa base:

  ```bash
  set -o pipefail
  age --decrypt -i "$AGE_IDENTITY" "$BACKUP_FILE" | \
    pg_restore --dbname="service=sdiego_restore" --exit-on-error --no-owner --no-privileges
  ```

  Prohibido apuntar este comando a producción. Verificar tablas, restricciones,
  secuencias, migraciones, agregados financieros, archivos y permisos del destino.
  Registrar checksum, fecha, resultado, tiempo de restauración y comparación de
  integridad sin datos personales. Un archivo o checksum **no acredita restauración**.
- La frecuencia, retención, RPO y RTO requieren aprobación; no crear timers todavía.

## Smoke tests y rollback

- Sin sesión: `/api/auth/session/` devuelve JSON anónimo y CSRF; rutas administrativas
  Vue redirigen al login. GET no expone datos financieros anónimos.
- Login autorizado: cookie de sesión Secure/HttpOnly/SameSite, CSRF Secure, rotación,
  recuperación al recargar y ausencia de credenciales en almacenamiento web.
- Login inválido y usuario inactivo no permiten distinguir usuarios existentes.
  No se aplica limitación por IP, por decisión del propietario. La prueba de login
  válido queda pendiente hasta que el propietario cree el administrador; no crear
  un usuario de demostración para este primer despliegue.
- POST de login sin CSRF se rechaza. Logout solo por POST con CSRF e invalida sesión.
- 401 y sesión expirada solicitan login; 403 con sesión activa conserva la sesión.
  No repetir operaciones financieras ni permitir reintentos de escritura en el proxy.
- No publicar `.env`, `.git`, medios ni backend; Vue y Django conservan sus rutas.
- Después de cada cambio compartido, RDBHS `/accounts/login/` y Superset `/health`
  deben devolver el estado esperado; comprobar también unidades y listeners.

Rollback: detener únicamente San Diego si hay riesgo de escrituras; conservar DB y
archivos. Volver al enlace y entorno previo como un par, verificando compatibilidad
con el esquema; reiniciar solo su servicio. Mantener la SECRET_KEY estable y los
estáticos correspondientes. No revertir migraciones ni restaurar una base anterior
automáticamente si hubo operaciones nuevas. Si es el primer despliegue, retirar o
mantener deshabilitado solo el sitio San Diego. Nunca reemplazar todo Caddyfile por
un backup antiguo si otros sitios cambiaron. Validar la copia que elimina únicamente
el bloque nuevo antes de una recarga autorizada; comprobar RDBHS/Superset.

## Referencias

- https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/
- https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- https://caddyserver.com/docs/caddyfile/directives/log
- https://www.postgresql.org/docs/17/backup.html
