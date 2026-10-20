# 🎬 Fábrica de Reels Automatizados con IA (AI Reel Factory)

Sistema completo y automatizado en Python para generar videos verticales de alto impacto (1080x1920 @ 30fps) listos para publicar en **Instagram Reels, TikTok y YouTube Shorts**.

---

## 🚀 Características Destacadas

- 🧠 **Generador de Guiones con IA**: Estructura de alta retención (*apertura concreta $\rightarrow$ desarrollo progresivo $\rightarrow$ cierre natural*) compatible con **Gemini, GPT-4o, Groq** y con **Modo Offline Autónomo** (no requiere API keys obligatorias).
- 📏 **Contrato Editorial de Guion**: Cada guion tiene presupuesto de palabras, número de escenas y tolerancia de duración verificables antes de renderizar; además bloquea fórmulas sensacionalistas y cierres forzados.
- 🎙️ **Locución Neuronal con Timestamps**: Integración con **Edge-TTS** (gratis y sin límites) con voces en español de México, España, Colombia, Argentina, etc. Extrae marcas de tiempo exactas palabra por palabra.
- 🎨 **Subtítulos Dinámicos Virales**: Estilo Alex Hormozi / TikTok en formato ASS con iluminación de color palabra por palabra en tiempo real (Amarillo Pop, Verde Neón, Cyan Eléctrico, Rojo Fuego).
- 🖼️ **Generador de Imágenes 9:16**: Integración con **Pollinations Flux** para crear imágenes cinematográficas en vertical con efecto dinámico **Ken Burns** (zoom suave).
- 🎵 **Mezcla de Audio Inteligente**: Fade in/out y auto-ducking (atenúa la música cuando el locutor habla).
- 🏭 **Modo Fábrica (Batch)**: Genera 5, 10 o decenas de videos en masa con un solo clic.
- 🖥️ **Interfaz Web Streamlit**: Panel de control moderno con reproductor de video, editor de guión y galería de exportaciones.

---

## 📦 Uso

```bash
python run.py                         # Interfaz web en http://localhost:8501
python run.py --check                 # Diagnóstico: FFmpeg, claves LLM, Pexels
python run.py --cli --topic "Por qué los pulpos tienen tres corazones" --niche ciencia --duration 30
```

### Crear un video (el flujo normal)
En la web: elige nicho → escribe un tema o pulsa 🎲 → plataforma (TikTok / Reels) → «Generar con IA» →
revisa el guion → «Fabricar». Al terminar tienes el MP4 para descargar y el texto (caption + hashtags) con
botón de copiar, listo para publicar a mano.

Por consola:
```bash
python run.py --cli --topic aleatorio --niche finanzas --platform tiktok        # tema aleatorio sin repetir
python run.py --cli --topic "La caída de Constantinopla" --niche historia_oculta --research   # documental
python run.py --cli --topic aleatorio --niche ciencia --platform reels --languages es,en      # también en inglés
```
- **Tema aleatorio**: con IA propone temas nuevos del nicho; sin IA usa `config/topic_bank.yaml` (editable).
  Los temas usados se guardan en `output/topic_history.json` y no se repiten.
- **Plataformas** (`config.yaml > platforms`): `tiktok` = 65-90 s (el Programa de Creadores paga solo videos
  de más de 1 minuto), `reels` = 30-60 s.
- **Documentales** (`--research`, por defecto en historia, misterio y ciencia): investiga en Wikipedia (y
  en Google con Gemini), escribe solo con hechos del dossier, verifica el guion y usa imágenes de archivo
  de Wikimedia Commons. Fuentes y créditos quedan en `publicacion.json` para la descripción.
- **Inglés**: adapta el guion (necesita un guionista IA) y reutiliza los mismos visuales.

### Calidad visual
- Clips reales de Pexels (y Pixabay con `PIXABAY_API_KEY`) cuando la escena es filmable; foto de Pexels si
  falla la imagen IA.
- Imágenes IA mejoradas con Real-ESRGAN (`tools/realesrgan/`, descargado de las releases oficiales de
  xinntao/Real-ESRGAN; funciona en GPU AMD/NVIDIA/Intel vía Vulkan, ~14 s por imagen).
- Exposición automática (los nichos sombríos conservan su ambiente pero legible), nitidez y un plano nuevo
  cada ~3,5 s.

### Reanudar un video interrumpido
Cada video guarda su progreso en `output/<job_id>/manifest.json`. Si algo falla, relanza con el mismo id
y continúa desde el último paso completado (no repite guion, voz ni imágenes ya descargadas):
```bash
python run.py --cli --topic "..." --job-id reel_20260923_075526_Por_que_los_pulpos_tienen
```

### Producción en lote
```bash
python run.py --batch temas.txt --workers 2        # un tema por línea: "tema | nicho | duración"
python run.py --batch temas.csv                    # columnas: topic,niche,duration,voice,aspect_ratio
python run.py --auto-topics 10 --niche finanzas    # la IA propone los temas
```
- Repetir el mismo lote (mismo archivo o `--batch-name`) **salta los videos terminados** y reintenta solo los fallidos.
- Informe por lote en `output/lotes/<lote>.csv`.
- `--workers`: videos en paralelo (1-3 según tu CPU). Sin `POLLINATIONS_TOKEN` las imágenes se piden de una en una entre todos los procesos.

### Qué produce cada video
- `output/<job_id>.mp4` — video final (H.264 + AAC, volumen normalizado a -14 LUFS, `faststart`).
- `output/<job_id>/publicacion.json` — título, caption y hashtags listos para pegar.
- `output/<job_id>/manifest.json` — estado (`completed`, `degraded` si hubo avisos, `failed`) y avisos concretos.

### Tests
```bash
python -m unittest tests.test_script_quality tests.test_pipeline_logic -v   # sin red, <1 s
python -m unittest tests.qa_full_suite -v                                   # integración (Edge-TTS + FFmpeg)
```

### Rendimiento
- Codificación final por GPU automática (NVIDIA / Intel / AMD) con respaldo a CPU: `video.encoder` en `config.yaml`.
- El cuello de botella son las imágenes de Pollinations (~45 s cada una en el nivel anónimo). Para acelerar:
  un `POLLINATIONS_TOKEN` gratuito permite pedirlas en paralelo, y con un LLM activo el director usa clips de
  Pexels (segundos) para las escenas filmables.

---

## 📁 Estructura del Proyecto

```
videos automatizados/
├── assets/
│   ├── fonts/               # Fuentes personalizadas (Arial, Montserrat, etc.)
│   ├── music/               # Pistas de música de fondo (.mp3, .wav)
│   ├── sfx/                 # Efectos de sonido
│   └── backgrounds/         # Fondos de video opcionales
├── config/
│   └── config.yaml          # Ajustes de dimensiones, voces, subtítulos y nichos
├── src/
│   ├── core/
│   │   ├── llm_client.py        # Cascada de proveedores LLM
│   │   ├── script_generator.py  # Guionista IA + modo offline
│   │   ├── tts_engine.py        # Síntesis de voz & Word Timings
│   │   ├── media_engine.py      # Generador de imágenes IA (Flux)
│   │   ├── subtitles_engine.py  # Subtítulos dinámicos ASS
│   │   ├── audio_engine.py      # Mezcla de audio & Ducking
│   │   └── video_renderer.py    # Ensamble FFmpeg
│   ├── pipeline.py              # Orquestador con manifiesto y reanudación
│   ├── batch.py                 # Producción en lote multiproceso
│   └── ui/
│       └── app.py               # Panel web Streamlit
├── output/                      # Reels exportados en MP4 1080x1920
├── .env                         # Claves de API opcionales (Gemini, OpenAI, Pexels)
├── requirements.txt             # Dependencias Python
└── run.py                       # Lanzador principal
```

---

## ⚙️ Personalización

- **Música de fondo**: Simplemente añade tus canciones en formato `.mp3` o `.wav` dentro de la carpeta `assets/music/`. El sistema las detectará y mezclará automáticamente con auto-ducking.
- **Voces y Nichos**: Puedes configurar nuevas voces o nichos en `config/config.yaml`.

---

## 📤 Publicación automática (Instagram, Facebook, YouTube)

Conecta tus cuentas en **⚙️ Ajustes → Conectar Redes Sociales** (los pasos están en la propia app). Desde entonces cada video que termine se publica solo en las redes de `config/config.yaml → publishing.platforms`, con su texto, hashtags y créditos de imágenes. El resultado (enlace o error) queda en `output/<job_id>/publicacion.json`; el botón **📤 Publicar ahora** reintenta solo las redes que fallaron.

- No se publica solo un video con guion genérico (la IA no respondió) ni con imágenes de respaldo: se queda esperando tu revisión.
- YouTube publica en **privado** hasta que tu proyecto de Google Cloud pase la auditoría gratuita de YouTube API Services.
- TikTok no está incluido: su API solo publica en privado sin una auditoría de empresa.
- La versión en inglés (si la generas) no se publica automáticamente: iría a las mismas cuentas.

## 🖼️ Posts de imagen (Instagram + Facebook)

Pestaña **🖼️ Crear Post** o `python run.py --post --topic aleatorio --niche misterio`. La IA escribe un titular (4-12 palabras) que se compone en grande sobre una imagen 4:5. La imagen se elige de lo más real a lo menos: la foto principal (con licencia libre) del artículo de Wikipedia del tema investigado, una foto de Pexels y, solo si no hay ninguna, una imagen IA de Pollinations y el texto completo va en la descripción con hashtags y fuentes. Se publica solo como los reels; un post con imagen de respaldo o texto genérico no se publica sin revisión. Para Instagram, la imagen se aloja como foto **no publicada** de tu página de Facebook (Instagram exige una URL pública).
