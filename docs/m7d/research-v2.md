# Research v2 experimental

Research v2 se puede probar en la PWA de loopback. Viene desactivado. La casilla **Use experimental Research v2** aparece cuando FastAPI anuncia la función y la PWA tiene habilitada la investigación local. Se activa por sesión de la interfaz. Desmarcarla vuelve al flujo habitual.

Research v2 interpreta las preguntas predeterminadas de la interfaz con el catálogo y valida el resultado contra los filtros seleccionados. Para las preguntas editadas, Gemini recibe la pregunta original y los límites de la consulta. Mantené claves, credenciales y texto privado fuera de las preguntas. La llamada de redacción recibe únicamente los enunciados autorizados y sus metadatos públicos. [Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash?hl=en)

## Activación local

Usá Python 3.12, las dependencias de `backend/requirements-ai.txt` y las dependencias de `frontend`. Ejecutá ambas terminales con el launcher detenido si ocupa los puertos 8000 o 5173.

**Terminal A, desde la raíz del repositorio.** Con PostgreSQL local, usá un rol de lectura y una base ya poblada con las series. El ingreso enmascarado mantiene las credenciales fuera del historial de comandos.

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
$env:MARKETPULSE_MODE = 'local'
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
$env:MARKETPULSE_RESEARCH_V2_ENABLED = 'true'
$env:MARKETPULSE_GEMINI_MODEL = 'gemini-3.6-flash'
$env:MACRO_API_MODE = 'loopback'
$env:MACRO_API_DSN = Read-Host -MaskInput 'DSN local de solo lectura'
$env:GEMINI_API_KEY = Read-Host -MaskInput 'Gemini API key'
try {
    .\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
} finally {
    Remove-Item Env:MACRO_API_DSN,Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
```

Si la instalación ya utiliza las series capturadas en Supabase DEV, podés usar la configuración de lectura autorizada existente. Establecé `MACRO_API_MODE=supabase-dev` y ejecutá `.\.venv\Scripts\python.exe scripts/dev-backend.py` en lugar de invocar Uvicorn directamente. Ese script carga la configuración de DEV en el proceso de backend. El rol lector usa TLS verificado y transacciones PostgreSQL `READ ONLY`. Esta prueba usa capturas existentes.

**Terminal B, desde la raíz.**

```powershell
Set-Location frontend
npm.cmd ci
$env:MARKETPULSE_MACRO_AI_ENABLED = 'true'
npm.cmd run dev
```

Abrí [la PWA local](http://127.0.0.1:5173/), marcá **Use experimental Research v2** y usá el mismo selector de indicador, evento y pregunta. La ventana del evento limita la consulta de investigación. El período seleccionado en el gráfico sigue controlando la vista del gráfico. El flag de v2 se lee en el backend. El modo público y los envíos autenticados desde el proxy LAN quedan fuera del alcance de v2.

Para revertir la activación del backend, detenelo, quitá `MARKETPULSE_RESEARCH_V2_ENABLED` y reiniciá. La casilla desaparece después de recargar la PWA. Podés usar `Remove-Item Env:MARKETPULSE_RESEARCH_V2_ENABLED` en la terminal A. El flujo habitual sigue disponible con `MARKETPULSE_MACRO_AI_ENABLED=true`.

## Prueba manual con Gemini real

Con la PWA y FastAPI iniciados como arriba, ejecutá estas cinco preguntas en **Ask LLM Model**. Comprobá que los detalles de una respuesta generada indiquen `gemini-3.6-flash`, que los enlaces abran las fuentes y que las limitaciones correspondan al resultado. Una respuesta **Data summary** indica que se sirvió la alternativa determinista.

| Caso | Indicador y evento | Pregunta | Qué comprobar |
| --- | --- | --- | --- |
| A | Eurozone Inflation · Ukraine War | How did the war in Ukraine relate to euro-area inflation? | HICP de Eurostat, diagnóstico del BCE sobre energía y otros factores, y una limitación explícita sobre la atribución causal. |
| B | Eurozone Inflation · Ukraine War | How much did euro-area annual inflation increase between September and October 2022? | Dos operandos mensuales, ambos de la misma serie, y octubre menos septiembre en puntos porcentuales. Con 9,9 % y 10,6 % la diferencia es +0,7 puntos. |
| C | Eurozone GDP · COVID-19 | What happened to euro-area GDP during the COVID-19 shock? | Crecimiento real trimestral, caída y rebote visibles si las observaciones están disponibles, y un comunicado de Eurostat con su propia edición y geografía. La diferencia entre tasas se expresa en puntos porcentuales. |
| D | Eurozone GDP · Generative AI Boom | How much did generative AI increase euro-area GDP? | Ausencia de una estimación causal. El caso simulado recupera una encuesta de adopción en la UE como contexto proxy. |
| E | EUR/USD rate · COVID-19 | What happened to EUR/USD during the pandemic? | Observaciones de `EXR.D.USD.EUR.SP00.A`, variación con sus fechas y la limitación de cobertura documental. La cita estadística lleva a la serie spot del BCE. |

En B, C y E, las cifras dependen de la captura local. Revisá **Technical details** para comprobar la edición disponible y los períodos efectivamente usados. En C, la ventana del evento comienza en febrero de 2020 y el lector exige trimestres enteros dentro de ella. Por eso la comparación de la serie empieza en el segundo trimestre cuando los datos están completos.

La suite con Gemini simulado y observaciones sintéticas fija las citas admitidas para los cinco ejemplos y cubre los fallos del proveedor, del recuperador y del lector oficial. Incluye una prueba del adaptador real de LangChain con el transporte del proveedor simulado.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_research_v2.py
npm.cmd --prefix frontend test -- src/MacroWorkspace.test.tsx
```

## Recuperación y soporte

El grafo interpreta, valida el alcance, lee series oficiales, recupera contexto, evalúa soporte, redacta y valida la respuesta. Usa el catálogo BCE/Eurostat existente. Una comparación mensual exige exactamente los dos meses especificados. Una tasa interanual y una diferencia entre tasas tienen unidades distintas. Los niveles, ajustes, subgrupos y restricciones de edición solo pueden contestarse cuando el contrato de la serie los respalda. Las partes sin cobertura se indican como limitaciones.

M7.C valida los 24 documentos y 47 afirmaciones aprobadas de M7.B y filtra por alcance y fecha. Research v2 exige la huella de esa edición. TF-IDF es el valor por defecto. Para probar E5, prepará primero la caché y el índice local según la [guía de M7.C](../m7c/README.md). En la terminal del backend, configurá `MARKETPULSE_RESEARCH_V2_RETRIEVER=e5` y `MARKETPULSE_RESEARCH_V2_E5_CACHE` con la ruta al directorio de caché. El índice se lee en `data/m7c/index.sqlite3` o en `MARKETPULSE_RESEARCH_V2_E5_INDEX` si está definida. Si la caché o el índice fallan, siguen disponibles las observaciones oficiales que se pudieron verificar.

Los resultados documentales tienen ID de afirmación y documento, evento, indicador, geografía, tipo de relación, fecha de publicación, URL, localizador y calificador editorial. Las afirmaciones recuperadas se presentan en su alcance original como contexto documental, contexto proxy o interpretación institucional. Los predicados de M7.D.1 ayudan a seleccionar contexto del tema. Las cifras principales proceden de observaciones oficiales y operaciones deterministas. La interpretación de un mecanismo por una institución se mantiene atribuida a esa institución. El corte de publicaciones es inclusivo al día y se limita al fin de la ventana y al día UTC del servidor.

Gemini elige orden y redacción dentro de enunciados autorizados, con un máximo de dos llamadas de hasta 18 segundos cada una y un intento por llamada. El servidor exige que cada enunciado coincida con una opción permitida para su ID y agrega los vínculos desde las fuentes verificadas. Una redacción inválida o una llamada fallida deja disponible el resumen determinista cuando hay evidencia. El grafo tiene rutas acotadas de evidencia parcial y abstención. La consulta usa el corpus local y las capturas existentes.

## Limitaciones conocidas

- El resultado sigue siendo experimental. La interpretación de Gemini puede omitir un calificador o entender mal una pregunta. La validación coteja el plan estructurado con el catálogo, los operandos y los filtros, y el texto final con enunciados autorizados. La prueba manual permite observar el comportamiento del modelo real.
- Las comparaciones usan las capturas locales disponibles al momento de consultar. Una serie revisada después del período puede diferir de un comunicado histórico. La fecha de actualización y la fecha de publicación se muestran por separado.
- La cobertura es la del catálogo y la edición aprobada de M7.B. El contexto de adopción en la UE no identifica el efecto de la IA generativa sobre el PIB de la zona euro. Las observaciones EUR/USD carecen de contexto documental aprobado sobre sus causas.
- M7.D.1 conserva el veredicto **REJECT**. Sus P1 finales incluyen confundir el nivel del índice HICP con la tasa interanual y perder una restricción conjunta de ajuste estacional con exclusión de calendario. Los informes también registran las limitaciones de variantes, promedios, vigencia y poblaciones detectadas en rondas anteriores. [Informe estadístico](statistical-definition-fix-report.md), [informe del gate](evidence-gate-fix-report.md) y [auditoría inicial](audit-report.md).
