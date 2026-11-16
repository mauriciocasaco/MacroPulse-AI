# Vectores de atestación v1

`origin-attestation-v1.json` contiene claves públicas sintéticas y vectores compartidos por Node y pytest. Decodificar `body_base64` y utilizar los bytes resultantes, incluidos espacios y saltos de línea. El vector administrativo incluye UTF-8 y CRLF para detectar cambios de codificación o reserialización. La IP IPv6 también conserva su ortografía en la firma.

Recalcular el digest del cuerpo, el mensaje canónico, el compromiso y la HMAC a partir de los campos de la petición. Los campos esperados sirven para detectar discrepancias entre lenguajes. Los casos de frescura solo prueban la ventana temporal y se aplican a los dos vectores. El callback y los campos del Durable Object permiten reutilizar el compromiso en pruebas de vínculo y consumo.

Usar los valores exclusivamente en pruebas. Las peticiones deben ejecutarse con relojes sintéticos, sin consultar servicios externos.
