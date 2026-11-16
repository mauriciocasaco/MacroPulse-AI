# Vectores de atestación v1

`origin-attestation-v1.json` contiene claves públicas sintéticas y vectores compartidos por Node y pytest. Decodificar `body_base64` y utilizar los bytes resultantes, incluidos espacios y saltos de línea. El vector administrativo incluye UTF-8 y CRLF para detectar cambios de codificación o reserialización. La IP IPv6 también conserva su ortografía en la firma.

Recalcular el digest del cuerpo, el mensaje canónico, el compromiso y la HMAC a partir de los campos de la petición. Los campos esperados sirven para detectar discrepancias entre lenguajes. Los casos de frescura solo prueban la ventana temporal y se aplican a los dos vectores. El callback y los campos del Durable Object permiten reutilizar el compromiso en pruebas de vínculo y consumo.

Usar los valores exclusivamente en pruebas. Los vectores usan relojes sintéticos, sin consultar servicios externos.

## Prueba con el runtime Worker

Para ejecutar el flujo Worker → Durable Object → FastAPI en local, usar Node 24, el entorno Python de tests y Miniflare 5.20261113.0-beta instalado en un directorio de herramientas. Definir `MACROPULSE_MINIFLARE_ENTRY` con la ruta absoluta a `node_modules/miniflare/dist/src/index.js` y ejecutar desde la raíz:

```sh
python -m pytest -q tests/test_origin_attestation_workerd.py
```

El runtime usa SQLite en el directorio temporal de pytest y se reinicia sobre ese almacén. El servidor de prueba escucha solo en loopback. El harness intercepta todas las llamadas salientes de workerd y conecta FastAPI por un transporte sintético. Las credenciales son los vectores públicos de prueba.
