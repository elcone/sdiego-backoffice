# Preflight final — primer despliegue

**Solo preparación. No se ejecutaron los comandos de instalación, transferencia,
backup, restauración, DNS, creación de usuarios/bases, migración ni publicación.**
La infraestructura y permisos siguen siendo los definidos en README.md.

## Decisiones registradas

- Dominio `privadasandiego.elcone.dev`; Caddy existente y HTTPS automático.
- Django/Gunicorn/systemd solo en `127.0.0.1:8002`; PostgreSQL exclusivo.
- Vue se compila exclusivamente en la estación local; Caddy sirve sus archivos.
- Sin limitación de login por IP, conforme a decisión explícita. No está pendiente
  implementar un limitador ni crear una tabla o servicio para ese propósito.
- Sin casas, cargos, movimientos, demostraciones, usuarios ni historial iniciales.
- El propietario creará el administrador después; CSV será una etapa independiente.
- core.0003 se conserva intacta y sus dos configuraciones están aceptadas.
  Ejecutar normalmente las migraciones, sin --fake ni eliminación posterior.

## DNS y certificados

Consulta pública contrastada con Cloudflare, Google y ns43.domaincontrol.com:
A y AAAA devuelven NXDOMAIN. No hay registro de destino ni conflicto CNAME visible.
El DNS autoritativo es GoDaddy (ns43/ns44.domaincontrol.com). No se encontró CAA
restrictivo en el dominio padre elcone.dev. La IPv4 pública del VPS coincide con
el A de su hostname SSH.

Registro que debe crear el propietario en la zona **elcone.dev**, tras autorización:

| Tipo | Nombre/Host | Valor | TTL |
| --- | --- | --- | --- |
| A | privadasandiego | 160.153.179.18 | Predeterminado del proveedor |

No crear AAAA hasta verificar IPv6 global, ruta y acceso al VPS. No modificar A/AAAA
ni CNAME de RDBHS/Superset. Tener presente la caché negativa DNS observada de 600 s.

Caddy escucha en 80/443. Desde fuera del VPS, el sitio existente responde 308 en
HTTP/80 y 200 en HTTPS/443 con certificado verificado. Eso confirma conectividad;
**todavía no hay certificado emitido ni validación ACME del dominio nuevo**.
Faltan el A público correcto y aplicar el bloque solo durante el despliegue autorizado.
El fragmento final fue adaptado y validado con `caddy validate` de Caddy 2.11.4,
como usuario caddy y mediante stdin: resultado `Valid configuration`. No se arrancó
el candidato, no se aplicó configuración, no se emitieron certificados ni se
recargaron servicios. La validación integrada completa
se hará sobre una copia del Caddyfile vigente en la fase autorizada.

Comprobaciones futuras de lectura:

```bash
dig @1.1.1.1 privadasandiego.elcone.dev A +short
dig @8.8.8.8 privadasandiego.elcone.dev A +short
dig @1.1.1.1 privadasandiego.elcone.dev AAAA +short
```

## Diagnóstico exacto de core.0003

Archivo intacto: `core/migrations/0003_initial_fee_configuration.py`.
Depende de core.0002 e inserta exclusivamente estas filas de core_feeconfiguration:

| valid_from | valid_until | monthly_fee | late_fee | payment_due_day |
| --- | --- | --- | --- | --- |
| 2026-01-01 | 2026-01-31 | 0.00 | 150.00 | 5 |
| 2026-02-01 | NULL | 1000.00 | 150.00 | 5 |

No accede a archivos, CSV, APIs ni otros datos externos. No crea casas, cargos,
pagos o movimientos. Omite filas ya idénticas y detiene la ejecución ante periodos
solapados incompatibles. Su reverso es noop: deshacerla no eliminaría esas filas.

No es necesaria para crear la estructura de FeeConfiguration (ya existe en 0001),
pero forma parte obligatoria del grafo vigente: core.0004 depende de 0003 y las
migraciones siguientes de core dependen de esa cadena. No omitirla, no usar --fake,
no retirar core de INSTALLED_APPS ni cambiar migraciones históricas.

**Decisión definitiva:** ejecutar normalmente todas las migraciones, incluida
core.0003, y conservar sus dos configuraciones aceptadas. No se hará limpieza ni
se alterará el grafo histórico. Tesorería v3 comienza sin casas, cargos ni movimientos;
la creación manual del administrador y las cargas CSV serán posteriores.

## Ensayo local de base nueva

Se ejecutó el migrate normal mediante el test runner de Django sobre PostgreSQL
**local**, en una base de prueba aleatoria que después se retiró. No se usó la base
real ni se creó una base en el VPS. No se cargaron fixtures ni CSV.

Resultado observado: 32 tablas; 28 migraciones aplicadas, 28 tipos de contenido y
112 permisos. Cantidades válidas para la revisión actual, sin usuarios adicionales.

| Tablas | Filas esperadas inmediatamente después de migrate |
| --- | --- |
| django_migrations | 28 |
| django_content_type | 28 |
| auth_permission | 112 |
| auth_group, auth_group_permissions | 0 en cada una |
| auth_user, auth_user_groups, auth_user_user_permissions | 0 en cada una |
| django_session, django_admin_log | 0 en cada una |
| core_feeconfiguration | **2; aceptadas** |
| core_house, core_contact, core_housecontact, core_payeraccount | 0 en cada una |
| core_banktransaction, core_chargeconcept, core_charge, core_chargeexclusion | 0 en cada una |
| core_payment, core_cfpayment, core_cfreconciliationgroup | 0 en cada una |
| core_cfgrouppayment, core_cfgroupdeposit | 0 en cada una |
| core_receiptevidence, core_paymentevidencelink, core_historicalhousecase | 0 en cada una |
| treasury_house, treasury_chargetype, treasury_expensetype | 0 en cada una |
| treasury_charge, treasury_banktransaction | 0 en cada una |

Las secuencias, índices, restricciones y funciones/triggers de las migraciones son
objetos técnicos de esquema; no constituyen cargas de transacciones. Las funciones
financieras existentes no se modifican. El administrador no se crea durante migrate.

## Revisiones del primer despliegue

- Backend productivo: el commit que incorpora esta preparación, con mensaje
  `chore: prepare production release`, en refactor/treasury-v3. Su SHA completo se
  registra en el reporte de entrega. Ejecutar los comandos desde un checkout de
  **ese commit**, no desde una punta posterior de la rama.
- El comando `git rev-parse HEAD` obtiene ese SHA completo; el procedimiento verifica
  además el mensaje del commit y la existencia del módulo productivo versionado.
  No se incrusta el hash de un commit dentro de sí mismo.
- Frontend aprobado: `641b6446cf9ce9ff6592d954286bd189158816e9`, rama main.
- Archivos incluidos: pyproject.toml, uv.lock, settings.py; settings_production.py,
  production_logging.py, test_production_settings.py y los archivos deploy/.
- Excluir AGENTS.md sin seguimiento, mocks frontend, iconos ajenos y proxy local.
- .env y credenciales reales no están versionados; backend.env.example solo tiene
  marcadores. No copiar ningún entorno real dentro de los archivos de release.

## Comandos de preparación local y primer despliegue futuro

**No ejecutar instalación/publicación hasta resolver los bloqueos y autorizarla.**
Utilizar el SHA productivo completo del reporte de entrega; no archivar el backend
base anterior a la preparación. Los comandos se ejecutan desde ese checkout.

En la estación local, tras aprobar un commit productivo:

```bash
set -euo pipefail
BACKEND_SHA=$(git rev-parse 'HEAD^{commit}')
[[ $(git show -s --format=%s "$BACKEND_SHA") == 'chore: prepare production release' ]]
FRONTEND_SHA='641b6446cf9ce9ff6592d954286bd189158816e9'
[[ "$BACKEND_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ $(git rev-parse "$BACKEND_SHA^{commit}") == "$BACKEND_SHA" ]]
git cat-file -e "$BACKEND_SHA:sdiego_backoffice/settings_production.py"
ARTIFACTS=$(mktemp -d /tmp/sdiego-first-release.XXXXXX)
git archive --format=tar.gz --output="$ARTIFACTS/$BACKEND_SHA.backend.tar.gz" "$BACKEND_SHA"
(cd "$ARTIFACTS" && sha256sum "$BACKEND_SHA.backend.tar.gz" > "$BACKEND_SHA.backend.tar.gz.sha256")
./deploy/build_frontend.sh /ruta/al/sdiego-backoffice-frontend "$FRONTEND_SHA" "$ARTIFACTS"
```

El script usa git archive del SHA, npm ci local, pruebas, build y validación de dist.
Fue validado con Node 24.16.0/npm 11.13.0. Verificar el manifiesto y checksums. El
procedimiento remoto siguiente no instala ni ejecuta Node.js, npm o Vite.

Solo en la ejecución autorizada, crear el staging aislado y transferir los cuatro
archivos conocidos, sin rsync --delete ni sincronización de árboles de trabajo:

```bash
ssh datamanager@rdbhs.elcone.dev 'install -d -m 0700 /home/datamanager/sdiego-staging'
rsync -av -- "$ARTIFACTS/$BACKEND_SHA.backend.tar.gz" \
  "$ARTIFACTS/$BACKEND_SHA.backend.tar.gz.sha256" \
  "$ARTIFACTS/$FRONTEND_SHA.tar.gz" "$ARTIFACTS/$FRONTEND_SHA.tar.gz.sha256" \
  datamanager@rdbhs.elcone.dev:/home/datamanager/sdiego-staging/
```

En el VPS, solo después de aprobación, con ambos SHAs completos definidos:

```bash
set -euo pipefail
cd /home/datamanager/sdiego-staging
sha256sum -c "$BACKEND_SHA.backend.tar.gz.sha256"
sha256sum -c "$FRONTEND_SHA.tar.gz.sha256"
release="/srv/sdiego-backoffice/releases/$BACKEND_SHA-$FRONTEND_SHA"
# Detenerse si existen inesperadamente usuario, roles, base, unidad o current.
sudo useradd --system --user-group --home-dir /srv/sdiego-backoffice --no-create-home --shell /usr/sbin/nologin sdiego-backoffice
sudo install -d -o root -g root -m 0755 /srv/sdiego-backoffice /srv/sdiego-backoffice/releases "$release"
sudo install -d -o root -g sdiego-backoffice -m 0750 "$release/backend"
sudo install -d -o root -g root -m 0755 "$release/frontend" "$release/static"
sudo install -d -o root -g root -m 0700 /etc/sdiego-backoffice
sudo install -d -o sdiego-backoffice -g sdiego-backoffice -m 0700 /srv/sdiego-backoffice/shared /srv/sdiego-backoffice/shared/private-media
sudo tar -xzf "$BACKEND_SHA.backend.tar.gz" -C "$release/backend" --no-same-owner
sudo tar -xzf "$FRONTEND_SHA.tar.gz" -C "$release/frontend" --no-same-owner
sudo env UV_CACHE_DIR=/srv/sdiego-backoffice/uv-cache \
  /home/datamanager/.local/bin/uv sync --project "$release/backend" --locked --no-dev --extra production --python /usr/bin/python3
sudo chown -R root:sdiego-backoffice "$release/backend"
sudo chmod -R g+rX,o-rwx "$release/backend"
sudo find "$release/frontend" -type d -exec chmod 0755 {} +
sudo find "$release/frontend" -type f -exec chmod 0644 {} +
```

Después, en este orden y con las pausas indicadas:

1. Verificar/cifrar la copia previa del Caddyfile y evidencia de recuperación de los
   servicios existentes mediante su procedimiento, sin cambiar RDBHS/Superset.
2. Revisar y ejecutar el SQL preparado, sin roles/bases preexistentes:
   `sudo cat "$release/backend/deploy/postgresql.sql" | sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres`.
   El pipe permite que postgres lea el SQL sin acceso al directorio backend privado.
   Contraseñas solo mediante `\password sdiego_app` y `\password sdiego_migrator`
   en psql interactivo. No crear un usuario Django.
3. El propietario configura mediante editor/mecanismo protegido backend.env y
   migration.env, root:root 0600; SECRET_KEY estable y passwords no se imprimen.
   ALLOWED_HOSTS=privadasandiego.elcone.dev; STATIC_ROOT="$release/static";
   MEDIA_ROOT=/srv/sdiego-backoffice/shared/private-media. Para esta etapa sin correo,
   EMAIL_BACKEND=django.core.mail.backends.dummy.EmailBackend en ambos entornos;
   no se requieren ni se inventan valores SMTP. La entrega y recuperación por correo
   están deshabilitadas temporalmente; al habilitar SMTP se exigirán los cinco campos
   reales. HSTS utiliza el valor aprobado; no usar marcadores del ejemplo.
4. Definir esta función solo en la sesión operativa autorizada:

   ```bash
   sdiego_manage() {
     local environment_file=$1
     shift
     sudo systemd-run --quiet --wait --pipe --collect --uid=sdiego-backoffice \
       --property="EnvironmentFile=$environment_file" --working-directory="$release/backend" \
       "$release/backend/.venv/bin/python" manage.py "$@"
   }
   sdiego_manage /etc/sdiego-backoffice/migration.env migrate --plan
   # Incluye core.0003 intacta y sus dos configuraciones aceptadas.
   sdiego_manage /etc/sdiego-backoffice/migration.env migrate --noinput
   sdiego_manage /etc/sdiego-backoffice/backend.env check --deploy
   sdiego_manage /etc/sdiego-backoffice/backend.env migrate --check
   ```

   Ejecutar migrate normalmente, sin --fake ni limpieza de core.0003. No crear
   administradores ni cargar fixtures o ejecutar comandos de importación.
5. Recoger estáticos como instalación root, sin hacerlos escribibles por el runtime:

   ```bash
   sudo systemd-run --quiet --wait --pipe --collect --uid=root \
     --property=EnvironmentFile=/etc/sdiego-backoffice/backend.env \
     --working-directory="$release/backend" \
     "$release/backend/.venv/bin/python" manage.py collectstatic --noinput
   sudo find "$release/static" -type d -exec chmod 0755 {} +
   sudo find "$release/static" -type f -exec chmod 0644 {} +
   sudo ln -s "$release" /srv/sdiego-backoffice/current.next
   sudo mv -Tf /srv/sdiego-backoffice/current.next /srv/sdiego-backoffice/current
   sudo install -m 0644 "$release/backend/deploy/sdiego-backoffice.service" /etc/systemd/system/sdiego-backoffice.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now sdiego-backoffice.service
   ```

6. Conservar el inventario inicial de tablas/filas y verificar que solo hay registros
   aprobados. Comprobar rol runtime y listener loopback 8002. Probar sesión anónima,
   CSRF y rechazo HTTP local con Host definitivo, sin crear un usuario de pruebas:

   ```bash
   curl --fail --silent --show-error -o /dev/null -w 'session %{http_code}\n' \
     -H 'Host: privadasandiego.elcone.dev' \
     -H 'X-Forwarded-Proto: https' http://127.0.0.1:8002/api/auth/session/
   ```

7. El operador crea el A público únicamente tras autorización independiente. Esperar
   las respuestas DNS correctas, sin AAAA incorrecto. Preparar copia completa de Caddy
   preservando todos los bytes existentes y añadiendo solo el fragmento San Diego:

   ```bash
   baseline=$(sudo sha256sum /etc/caddy/Caddyfile | awk '{print $1}')
   { sudo cat /etc/caddy/Caddyfile; printf '\n'; sudo cat "$release/backend/deploy/Caddyfile.fragment"; } | \
     sudo tee /etc/caddy/Caddyfile.sdiego-proposed >/dev/null
   sudo caddy validate --config /etc/caddy/Caddyfile.sdiego-proposed --adapter caddyfile
   sudo diff -u /etc/caddy/Caddyfile /etc/caddy/Caddyfile.sdiego-proposed
   # diff devuelve 1 por la adición esperada; revisar antes de continuar.
   [[ $(sudo sha256sum /etc/caddy/Caddyfile | awk '{print $1}') == "$baseline" ]]
   sudo install -m 0644 /etc/caddy/Caddyfile.sdiego-proposed /etc/caddy/Caddyfile.sdiego-next
   sudo mv -T /etc/caddy/Caddyfile.sdiego-next /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

   Las líneas posteriores a diff se ejecutan por separado tras revisión, pues la
   sesión con set -e debe detenerse en ese diff esperado. No reiniciar Caddy ni
   reemplazar otros sitios. Registrar el hash del fragmento y el baseline.
8. Validar certificado y cookie CSRF Secure; sesión anónima, GET logout rechazado,
   POST login sin CSRF rechazado, SPA con recarga directa y /static/admin/css/base.css.
   Confirmar los prefijos /api/, /accounts/ y /admin/, sin reintentos de escritura.
   Login válido queda pendiente de la creación posterior del administrador por el
   propietario. No exponer ni imprimir cookies/valores de tokens en evidencia.
9. Verificar `systemctl is-active caddy postgresql@17-main rdbhs-backoffice superset`
   y HTTP de RDBHS/Superset. Registrar revisiones, checksum, resultados y alcance vacío.
   CSV y cualquier carga de información se autorizan aparte.

## Backup y restauración aislada (solo procedimiento)

Reutilizar age y el mecanismo existente de copia externa y snapshots. El procedimiento
está preparado, pero **no se realizó ningún respaldo ni restauración en este preflight**.
La existencia de dumps/cifrados históricos de RDBHS no acredita recuperación San Diego.
No solicitar ni crear snapshots GoDaddy sin autorización.

Después de existir una base San Diego, en una ejecución autorizada, guardar pg_dump
custom cifrado, checksum y manifiesto de revisión/conteos; cifrar también configuración,
entornos y medios privados mediante el mecanismo protegido existente. Sin datos iniciales
no hay una base San Diego previa que respaldar; antes de tocar Caddy proteger su baseline.

Para probar una copia, el operador prepara una base **aislada** fuera del entorno productivo
(por ejemplo sdiego_restore_<identificador>), con credenciales exclusivas en un servicio
libpq llamado sdiego_restore, archivo protegido. No usar el rol runtime ni la URL productiva.

```bash
set -euo pipefail
umask 077
restore_db=$(psql 'service=sdiego_restore' -XAt -c 'SELECT current_database()')
[[ "$restore_db" == sdiego_restore_* ]]
[[ $(psql 'service=sdiego_restore' -XAt -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'") == 0 ]]
sha256sum -c "$BACKUP_FILE.sha256"
age --decrypt -i "$AGE_IDENTITY" "$BACKUP_FILE" | \
  pg_restore --dbname='service=sdiego_restore' --exit-on-error --no-owner --no-privileges
```

Comprobar salida, tablas, claves/índices/triggers, secuencias y conteos contra el
manifiesto de origen; incluir ausencia de casas/cargos/movimientos/usuarios para la
copia inicial. Verificar por separado medios y configuración cifrada sin imprimirlos.
Registrar fecha, SHA256, duración, resultado y evidencia de comparación, sin datos
personales ni secretos. No declarar validado hasta completar estos pasos. Los backups
posteriores a crear el administrador deberán incluirlo: no comparar contra cero usuarios.

## Rollback específico del primer despliegue

- `sudo systemctl stop sdiego-backoffice.service`; no detener RDBHS/Superset/PostgreSQL.
- Preparar una copia del Caddyfile **vigente** que retire exclusivamente el bloque
  exacto añadido para privadasandiego.elcone.dev; comparar y comprobar que todos los
  demás bloques permanecen byte a byte. Si hubo cambios concurrentes, detenerse.
- Validar esa copia con caddy validate y, solo en la ejecución autorizada, reemplazar
  de forma atómica y recargar Caddy; no restaurar ciegamente un Caddyfile antiguo.
- Confirmar salud pública de RDBHS y Superset y listener 8001/8088, antes y después.
- Conservar base, medios, secretos y release para diagnóstico. No ejecutar migrate
  hacia atrás, drop database, borrar usuarios Linux ni importar un backup encima.
- No hay enlace anterior en un primer despliegue. Retirar la publicación y mantener
  San Diego detenido es el rollback; no inventar una revisión previamente desplegada.
- El DNS no se modifica automáticamente durante rollback.

## Validaciones del preflight final

- Ensayo local de migrate en base PostgreSQL aleatoria: inventario anterior confirmado.
- 111 pruebas de Tesorería, autenticación y configuración productiva: correctas.
- check --deploy con el dominio definitivo y secretos temporales en memoria: sin
  errores; W005 y W021 por no habilitar HSTS en subdominios ni preload. Sin checks
  silenciados; el valor temporal de prueba no constituye una política productiva.
- Gunicorn --check-config local: correcto; no se arrancó un servidor.
- makemigrations --check --dry-run: sin cambios. No se modificó core ni migraciones.
- Ruff lint/formato y Bash de los comandos documentados: correctos.
- Frontend del SHA fijado: npm ci, 51 pruebas, build y validación de dist correctos,
  exclusivamente locales. Artefacto previo reverificado por checksum y contenido.
- git diff --check y whitespace de archivos nuevos: correctos.
- Sin cambios en el Caddyfile activo, instalaciones, transferencias de artefactos,
  DNS, usuarios/bases productivas, reinicios, backups/restauraciones o push.

La validación conjunta de la copia de Caddyfile con los otros sitios y los permisos
reales del release requiere recursos instalados y queda para la ejecución autorizada.
Referencias: https://caddyserver.com/docs/command-line#caddy-validate y
https://caddyserver.com/docs/automatic-https.
