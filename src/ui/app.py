"""
Streamlit Web UI - Fábrica de Reels Automatizados con IA
Interfaz visual interactiva para creación individual y producción en masa (batch) de Reels.
"""

import os
import sys
import glob
import datetime
import streamlit as st

# Añadir directorio raíz al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.pipeline import ReelFactoryPipeline
from src.core.tts_engine import TTSEngine
from src.core.script_generator import ScriptGenerator, ReelScript, Scene
from src.core.niche_manager import NicheManager
from src.core.ai_editor import AIEditorCopilot
from src.core.topic_picker import is_random_topic
from src.core.env import save_env
from src.core import publisher


st.set_page_config(
    page_title="Fábrica de Reels con IA - Creative Director",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

niche_mgr = NicheManager()
all_niches = niche_mgr.get_all_niches()


# Estilos CSS personalizados para look cinematográfico moderno
st.markdown("""
<style>
    .main-header {
        font-size: 2.3rem;
        font-weight: 800;
        background: -webkit-linear-gradient(45deg, #FF4B4B, #FF914D, #FFCC00);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
    }
    .sub-header {
        color: #999;
        font-size: 1.05rem;
        margin-bottom: 20px;
    }
    .metric-card {
        background-color: #1E222D;
        border-radius: 12px;
        padding: 15px;
        border: 1px solid #2D3342;
        margin-bottom: 10px;
    }
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# Instanciar pipeline directamente para asegurar que siempre use la última versión
pipeline = ReelFactoryPipeline()


def show_generation_result(result, widget):
    """Muestra el estado real del trabajo: completado, o completado con avisos concretos."""
    label = f"({result.duration:.1f}s · guion: {result.script.source})"
    if result.status == "completed":
        widget.success(f"🎉 ¡Video listo! {label}")
    else:
        notes = "\n".join(f"- {w['message']}" for w in result.warnings)
        widget.warning(f"⚠️ Video listo con avisos {label}:\n{notes}")


def render_publication_panel(package_paths):
    """Por cada idioma: vista previa, descarga del MP4 y texto listo para copiar y publicar."""
    import json
    labels = {"es": "🇪🇸 Español", "en": "🇺🇸 English"}
    packages = []
    for path in package_paths:
        with open(path, "r", encoding="utf-8") as package_file:
            packages.append(json.load(package_file))
    tabs = st.tabs([labels.get(p.get("language"), p.get("language", "video")) for p in packages])
    for tab, package, package_path in zip(tabs, packages, package_paths):
        with tab:
            video = package.get("video", "")
            if not os.path.exists(video):
                st.warning("El archivo de video ya no existe.")
                continue
            st.video(video)
            with open(video, "rb") as vf:
                st.download_button("⬇️ Descargar MP4", data=vf.read(), file_name=os.path.basename(video),
                                   mime="video/mp4", use_container_width=True, key=f"dl_{video}")
            st.markdown("**📋 Texto para publicar** (botón de copiar arriba a la derecha):")
            st.code(package.get("caption_completo", ""), language=None, wrap_lines=True)
            if package.get("fuentes_y_creditos"):
                with st.expander("📚 Fuentes y créditos (pégalos en la descripción o en el primer comentario)"):
                    st.code(package["fuentes_y_creditos"], language=None, wrap_lines=True)
            st.caption(f"⏱️ {package.get('duration_seconds')} s · {package.get('platform') or 'personalizado'} · guion: {package.get('script_source')}")
            render_publication_status(package_path, package)


NETWORK_NAMES = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}


def render_publication_status(package_path, package):
    """Dónde quedó publicado el video y botón para publicar en las redes que falten."""
    done = package.get("publicado", {})
    for platform, info in done.items():
        name = NETWORK_NAMES.get(platform, platform)
        if info.get("ok"):
            st.success(f"✅ Publicado en {name}: {info.get('url', '')}")
        else:
            st.error(f"❌ {name}: {info.get('error', 'error desconocido')}")
    settings = pipeline.config.get("publishing", {})
    pending = [p for p in publisher.enabled_platforms(settings, package.get("type", "reel")) if not done.get(p, {}).get("ok")]
    if pending:
        label = ", ".join(NETWORK_NAMES[p] for p in pending)
        if st.button(f"📤 Publicar ahora en {label}", key=f"pub_{package_path}", use_container_width=True):
            with st.spinner(f"Publicando en {label} (Instagram puede tardar unos minutos en procesarlo)..."):
                publisher.publish_package(package_path, pending, settings.get("youtube_privacy", "public"))
            st.rerun()
    elif not publisher.connected_platforms():
        st.caption("📤 Conecta Instagram, Facebook o YouTube en ⚙️ Ajustes y los videos se publicarán solos al terminar.")


def run_auto_publish(package_path, widget):
    """Publicación automática al terminar un video (config.yaml → publishing.auto)."""
    if not package_path or not os.path.exists(package_path):
        return
    settings = pipeline.config.get("publishing", {})
    if not settings.get("auto") or not publisher.connected_platforms():
        return
    with st.spinner("📤 Publicando en tus redes (Instagram puede tardar unos minutos)..."):
        results, skipped = publisher.auto_publish(package_path, settings)
    if skipped:
        widget.warning(f"⚠️ {skipped}")
    for platform, info in results.items():
        name = NETWORK_NAMES.get(platform, platform)
        if info.get("ok"):
            widget.success(f"✅ Publicado en {name}: {info.get('url', '')}")
        else:
            widget.error(f"❌ No se pudo publicar en {name}: {info.get('error', '')}")


# --- SIDEBAR: Configuración Global ---
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/video-editing.png", width=64)
    st.markdown("### ⚙️ Configuración del Reel")

    voices = TTSEngine.list_spanish_voices()
    voice_options = {v["name"]: v["id"] for v in voices}
    selected_voice_name = st.selectbox(
        "🎙️ Voz del Locutor (IA)",
        options=list(voice_options.keys()),
        index=0
    )
    selected_voice_id = voice_options[selected_voice_name]

    sub_styles = {
        "Amarillo Viral (Alex Hormozi)": "yellow_pop",
        "Verde Neón (High Energy)": "green_neon",
        "Cyan Eléctrico (Tech / Space)": "cyan_electric",
        "Rojo Fuego (Misterio / Alerta)": "red_fire",
        "Blanco Puro (Clean Minimalist)": "white_pure",
        "🎬 Director IA (color por escena)": "director",
        "🚫 Desactivar Subtítulos (Video Limpio)": "none"
    }
    selected_sub_name = st.selectbox(
        "🎨 Estilo de Subtítulos",
        options=list(sub_styles.keys()),
        index=0
    )
    selected_sub_style = sub_styles[selected_sub_name]


    st.markdown("---")
    st.markdown("### 🔑 Estado de Servicios")
    llm_providers = pipeline.script_gen.llm.available_providers()
    pexels_ok = bool(os.getenv("PEXELS_API_KEY"))
    st.caption(f"• Guionista IA: {'🟢 ' + ', '.join(llm_providers) if llm_providers else '⚪ Sin clave (modo offline genérico)'}")
    st.caption("  (usa «Probar conexión» en Ajustes para validar las claves)")
    st.caption(f"• Pexels (clips reales): {'🟢 Configurado' if pexels_ok else '⚪ Sin API'}")
    st.caption("• Edge-TTS (Voces): 🟢 Gratuito")
    st.caption(f"• Pollinations (Imágenes): 🟢 {'Con token (paralelo)' if os.getenv('POLLINATIONS_TOKEN') else 'Anónimo (1 a la vez)'}")

# --- HEADER PRINCIPAL CON SKILLS ACTIVAS ---
st.markdown('<p class="main-header">🎬 Fábrica de Reels Automatizados con IA</p>', unsafe_allow_html=True)
st.markdown("""
<div style="background: linear-gradient(90deg, #1f2937, #111827); border-left: 4px solid #F59E0B; padding: 10px 16px; border-radius: 8px; margin-bottom: 18px;">
    <span style="font-weight: 700; color: #F59E0B;">⚡ Agente con Skills Especializadas Activas:</span>
    <span style="color: #E5E7EB; margin-left: 10px;">🎯 <b>Creatividad</b> (Hooks virales & retención)</span>
    <span style="color: #9CA3AF; margin: 0 6px;">|</span>
    <span style="color: #E5E7EB;">🎬 <b>Dirección de Arte</b> (Cinematografía 9:16 & 8k)</span>
    <span style="color: #9CA3AF; margin: 0 6px;">|</span>
    <span style="color: #E5E7EB;">✂️ <b>Edición de Contenido</b> (Cadencia rápida & subtítulos)</span>
</div>
""", unsafe_allow_html=True)

tab1, tab_post, tab_editor, tab2, tab_niches, tab3, tab4 = st.tabs([
    "🚀 Crear Video / Reel",
    "🖼️ Crear Post",
    "🎬 Editor IA (CapCut / InShot)",
    "🏭 Fábrica en Lote (Batch)",
    "🎯 Gestor de Nichos",
    "📁 Galería de Videos",
    "⚙️ Configuración & API Keys"
])

# ==========================================
# TAB 1: CREAR REEL INDIVIDUAL
# ==========================================
with tab1:
    col_left, col_right = st.columns([1.15, 0.85])

    with col_left:
        st.markdown("#### 1. Definir Contenido & Nicho")

        niche_keys = list(all_niches.keys())
        niche_labels = {k: all_niches[k].get("name", k) for k in niche_keys}

        niche_col, topic_col, dice_col = st.columns([1.1, 1.7, 0.4])
        with niche_col:
            selected_niche_key = st.selectbox(
                "Nicho / Categoría",
                options=niche_keys,
                format_func=lambda x: niche_labels.get(x, x)
            )
            current_niche_info = all_niches.get(selected_niche_key, {})
        with topic_col:
            if "topic_input" not in st.session_state:
                st.session_state["topic_input"] = "aleatorio"
            topic_input = st.text_input(
                "Tema del Reel (o escribe 'aleatorio')",
                key="topic_input",
                help="Ejemplo: Por qué soñamos, 3 hábitos de millonarios, La paradoja de Fermi... "
                     "Con 'aleatorio' la IA elige un tema concreto del nicho que no hayas usado."
            )
        with dice_col:
            def _roll_topic(niche_key=selected_niche_key):
                st.session_state["topic_input"] = pipeline.script_gen.topics.pick(niche_key, remember=False)

            st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
            st.button("🎲", help="Proponer un tema aleatorio del nicho (puedes editarlo)", on_click=_roll_topic, use_container_width=True)

        st.caption(f"🎭 **Tono:** {current_niche_info.get('tone', '')}")

        # 🎛️ SECCIÓN DE CONTROLES AVANZADOS Y LIBERTADES
        with st.expander("🎛️ Controles Avanzados (Duración, Formato, Subtítulos, Zoom & Audio)", expanded=True):
            st.markdown("##### ⏱️ Tipo de Producción & Duración")

            platform_labels = {
                "🎵 TikTok (65-90 s · monetizable)": "tiktok",
                "📸 Reels Instagram/Facebook (30-60 s)": "reels",
                "⚙️ Personalizado": None,
            }
            platform_choice = st.radio("Plataforma de destino:", list(platform_labels.keys()), horizontal=True)
            selected_platform = platform_labels[platform_choice]
            platform_cfg = pipeline.platform_profile(selected_platform)
            prod_mode = "Formato Corto" if selected_platform else st.radio(
                "Modalidad de Video:",
                ["📱 Formato Corto / Reels / Shorts (10s - 60s)", "📺 Formato Largo / Documental YouTube (1 min - 10 min)"],
                horizontal=True
            )

            opt_col1, opt_col2 = st.columns(2)
            with opt_col1:
                research_enabled = st.checkbox(
                    "🔎 Investigar antes de escribir (documental con fuentes)",
                    value=pipeline.research_by_default(selected_niche_key),
                    help="Busca en Wikipedia (y en Google si hay clave de Gemini) y escribe el guion solo con hechos verificados.",
                )
            with opt_col2:
                also_english = st.checkbox("🇺🇸 También en inglés", value=False,
                                           help="Adapta el guion al inglés y produce un segundo video con los mismos visuales.")

            dur_col1, dur_col2 = st.columns([1.2, 1.8])
            with dur_col1:
                if selected_platform:
                    target_duration = st.slider("Duración Objetivo (Segundos)", min_value=platform_cfg.get("min_duration", 30),
                                                max_value=platform_cfg.get("max_duration", 60),
                                                value=platform_cfg.get("default_duration", 45), step=5)
                    default_aspect_index = 0
                    max_scenes_allowed = 12
                elif "Formato Corto" in prod_mode:
                    target_duration = st.slider("Duración Objetivo (Segundos)", min_value=10, max_value=60, value=30, step=5)
                    default_aspect_index = 0 # 9:16
                    max_scenes_allowed = 10
                else: # Formato Largo YouTube
                    target_minutes = st.slider("Duración Objetivo (Minutos)", min_value=1, max_value=10, value=5, step=1, help="Genera documentales y videos largos estructurados para YouTube (1 a 10 minutos)")
                    target_duration = target_minutes * 60
                    default_aspect_index = 2 # 16:9
                    max_scenes_allowed = 30

            with dur_col2:
                scene_mode = st.radio(
                    "Autonomía en Estructura de Escenas:",
                    ["🤖 Libertad Creativa Total (La IA define las escenas para Inicio, Desarrollo y Clímax completos)", "🔢 Forzar número exacto de escenas"],
                    index=0
                )
                if "Libertad Creativa" in scene_mode:
                    scene_count = None
                else:
                    scene_count = st.slider("Cantidad de Escenas / Visuales", min_value=3, max_value=max_scenes_allowed, value=max(3, min(25, target_duration // 25)))

            voice_rate_choice = st.select_slider(
                "Velocidad de Locución",
                options=["-10%", "+0%", "+5%", "+12%", "+20%"],
                value="+5%"
            )

            # Información de presupuesto
            est_words = int(target_duration * 2.2)
            scenes_display = f"{scene_count} escenas fijas" if scene_count else "Autónomas según el arco narrativo"
            st.info(f"📊 **Plan de Producción:** `{target_duration}s` (~{target_duration//60} min {target_duration%60}s) | Presupuesto: `~{est_words} palabras` | Estructura: `{scenes_display}` (Acto 1: Inicio -> Acto 2: Desarrollo profundo -> Acto 3: Clímax y Desenlace).")


            st.markdown("---")
            st.markdown("##### 📐 Formato & Subtítulos")

            enable_subtitles = st.checkbox(
                "✨ Activar Subtítulos Dinámicos",
                value=(selected_sub_style != "none"),
                help="Desmarca esta casilla si deseas renderizar un video limpio sin texto superpuesto."
            )

            if enable_subtitles:
                fmt_col1, fmt_col2, fmt_col3 = st.columns(3)
                with fmt_col1:
                    aspect_ratio = st.selectbox(
                        "Formato de Video",
                        options=["9:16", "1:1", "16:9"],
                        index=default_aspect_index,
                        format_func=lambda x: "📱 Vertical 9:16 (Reels/TikTok/Shorts)" if x == "9:16" else ("🟦 Cuadrado 1:1 (Feed Post)" if x == "1:1" else "🖥️ Horizontal 16:9 (YouTube Estándar)")
                    )
                with fmt_col2:
                    sub_position = st.selectbox(
                        "Posición Subtítulos",
                        options=["bottom", "center", "top"],
                        format_func=lambda x: "Abajo (Zona Segura)" if x == "bottom" else ("Centro (Viral Impact)" if x == "center" else "Arriba")
                    )
                with fmt_col3:
                    words_per_line = st.slider("Palabras por línea", min_value=1, max_value=6, value=(4 if aspect_ratio == "16:9" else 3), help="1 palabra = estilo Alex Hormozi pop individual")

                sub_col1, sub_col2 = st.columns(2)
                with sub_col1:
                    default_fsize = 54 if aspect_ratio == "16:9" else 70
                    font_size = st.slider("Tamaño de Fuente Subtítulos", min_value=36, max_value=100, value=default_fsize)
                with sub_col2:
                    zoom_intensity = st.slider("Efecto Zoom Ken Burns", min_value=0.0, max_value=1.8, value=1.0, step=0.2, help="0.0 = imagen fija sin movimiento, 1.0 = zoom estándar, 1.5 = zoom dramático")
            else:
                st.caption("ℹ️ *Subtítulos desactivados. El video se renderizará limpio con solo visuales, locución y música de fondo.*")
                fmt_col1, fmt_col2 = st.columns(2)
                with fmt_col1:
                    aspect_ratio = st.selectbox(
                        "Formato de Video",
                        options=["9:16", "1:1", "16:9"],
                        index=default_aspect_index,
                        format_func=lambda x: "📱 Vertical 9:16 (Reels/TikTok/Shorts)" if x == "9:16" else ("🟦 Cuadrado 1:1 (Feed Post)" if x == "1:1" else "🖥️ Horizontal 16:9 (YouTube Estándar)")
                    )
                with fmt_col2:
                    zoom_intensity = st.slider("Efecto Zoom Ken Burns", min_value=0.0, max_value=1.8, value=1.0, step=0.2)
                sub_position = "bottom"
                words_per_line = 3
                font_size = 60

            st.markdown("---")
            st.markdown("##### 🎵 Música de Fondo")
            music_vol = st.slider("Volumen Música de Fondo", min_value=0.0, max_value=0.35, value=0.12, step=0.02)



        # Botones de Acción para el Guión
        st.markdown("##### 🚀 Opciones de Creación del Guión")
        btn_c1, btn_c2, btn_c3 = st.columns([1.2, 1, 1])

        with btn_c1:
            if st.button("✨ Paso 1: Generar con IA", type="primary", use_container_width=True, help="Genera un guión estructurado con dirección creativa usando IA"):
                with st.spinner("Creando guión estructurado aplicando Skills de Creatividad y Dirección..."):
                    # Se recuerdan los parámetros para poder reintentar si la IA gratuita está saturada.
                    st.session_state["last_draft_args"] = dict(
                        niche=selected_niche_key, target_duration=target_duration, scene_count=scene_count,
                        platform=selected_platform, research=research_enabled,
                    )
                    script_obj = pipeline.draft_script(
                        topic=topic_input,
                        niche=selected_niche_key,
                        target_duration=target_duration,
                        scene_count=scene_count,
                        platform=selected_platform,
                        research=research_enabled,
                    )
                    st.session_state["current_script"] = script_obj
                    st.session_state["script_warning"] = (
                        "⚠️ **Guion genérico**: la IA no respondió y se usó el modo offline. "
                        + pipeline.script_gen.llm.failure_summary()
                    ) if script_obj.source == "offline" else None
                    st.rerun()

        with btn_c2:
            if st.button("✍️ Crear Manualmente", use_container_width=True, help="Inicia un guión editable para que escribas tu propio texto y elijas tus visuales"):
                topic_title = pipeline.script_gen.topics.pick(selected_niche_key) if is_random_topic(topic_input) else topic_input.strip()
                manual_count = scene_count or (16 if target_duration >= 600 else (12 if target_duration >= 300 else (6 if target_duration > 60 else 3)))
                manual_scenes = []
                # Escena 1: Apertura
                manual_scenes.append(Scene(
                    scene_id=1,
                    text=f"Escribe el planteamiento inicial o apertura magnética sobre {topic_title}...",
                    image_prompt=f"Cinematic opening visual for {topic_title}, photorealistic 8k",
                    keywords=["inicio", "apertura"],
                    media_type="pexels_video",
                    pexels_query=f"{topic_title} cinematic 4k",
                    camera_motion="fast_punch_zoom",
                    transition_out="whip_pan_right",
                    visual_vfx="deep_vignette",
                    sfx_cue="impact_boom",
                    subtitle_mode="single_word_punch",
                    accent_color="&H0000E5FF&"
                ))
                # Escenas intermedias de desarrollo
                for m_idx in range(2, manual_count):
                    manual_scenes.append(Scene(
                        scene_id=m_idx,
                        text=f"Escribe el punto clave o evidencia #{m_idx} del desarrollo aquí...",
                        image_prompt=f"Detailed investigative storytelling visual for {topic_title}, atmospheric lighting 8k",
                        keywords=["desarrollo", f"punto_{m_idx}"],
                        media_type="ai_image" if m_idx % 2 == 0 else "pexels_video",
                        camera_motion="slow_dolly_in" if m_idx % 2 == 0 else "pan_right",
                        transition_out="crossfade" if m_idx % 2 == 0 else "whip_pan_left",
                        visual_vfx="cinematic_clean" if m_idx % 2 == 0 else "teal_orange",
                        sfx_cue="whoosh_fast" if m_idx % 2 == 0 else "none",
                        subtitle_mode="dynamic_pop",
                        accent_color="&H00FFFF00&" if m_idx % 2 == 0 else "&H003BF029&"
                    ))
                # Escena final de clímax
                if manual_count > 1:
                    manual_scenes.append(Scene(
                        scene_id=manual_count,
                        text="Escribe la conclusión, revelación final o remate definitivo...",
                        image_prompt=f"Epic victorious cinematic climax scene for {topic_title}, masterpiece 8k",
                        keywords=["climax", "final"],
                        media_type="ai_image",
                        camera_motion="slow_pull_out",
                        transition_out="flash_white",
                        visual_vfx="warm_vintage",
                        sfx_cue="sub_bass_drop",
                        subtitle_mode="single_word_punch",
                        accent_color="&H0000D7FF&"
                    ))
                st.session_state["current_script"] = ReelScript(
                    title=topic_title,
                    niche=selected_niche_key,
                    hook="Escribe aquí el gancho de alto impacto para capturar la atención inicial...",
                    scenes=manual_scenes,
                    cta="",
                    full_script_text=""
                )
                st.rerun()

        with btn_c3:
            if st.button("📂 Cargar Plantilla", use_container_width=True, help="Carga una plantilla profesional instantánea del nicho seleccionado"):
                topic_name = pipeline.script_gen.topics.pick(selected_niche_key) if is_random_topic(topic_input) else topic_input.strip()
                script_obj = pipeline.script_gen._generate_offline(
                    topic=topic_name,
                    niche=selected_niche_key,
                    target_duration=target_duration,
                    scene_count=scene_count
                )
                st.session_state["current_script"] = script_obj
                st.rerun()

        # Si aún no hay guión en sesión, mostrar guía de inicio
        if "current_script" not in st.session_state:
            st.info("💡 **¿Cómo empezar?** Elige arriba una opción: **Generar con IA**, redactar tu **Guión Manual**, o **Cargar Plantilla** para habilitar la edición de escenas y la fabricación del video.")

        # Si hay guión en sesión, mostrarlo para edición
        if "current_script" in st.session_state:
            s: ReelScript = st.session_state["current_script"]
            if st.session_state.get("script_warning"):
                st.warning(st.session_state["script_warning"])
                st.caption("Los niveles gratuitos de IA se saturan a ratos; suele bastar con esperar 1-2 minutos.")
                if st.button("🔁 Reintentar con IA (mismo tema)", key="retry_ai_script") and st.session_state.get("last_draft_args"):
                    with st.spinner("Reintentando con los guionistas de IA disponibles..."):
                        retried = pipeline.draft_script(topic=s.title, **st.session_state["last_draft_args"])
                    st.session_state["current_script"] = retried
                    st.session_state["script_warning"] = (
                        "⚠️ **Guion genérico**: la IA sigue sin responder. " + pipeline.script_gen.llm.failure_summary()
                    ) if retried.source == "offline" else None
                    st.session_state.pop("editor_state", None)
                    st.rerun()
            elif s.source not in ("offline", "dossier"):
                st.caption(f"✍️ Guion escrito por IA ({s.source}).")
            st.markdown("---")

            sec2_col1, sec2_col2 = st.columns([1.6, 1])
            with sec2_col1:
                st.markdown("#### 2. Revisar y Modificar Guión en Vivo")
            with sec2_col2:
                if st.button("🗑️ Descartar / Borrar Guión", help="Elimina el guión actual de la memoria sin generar video"):
                    if "current_script" in st.session_state:
                        del st.session_state["current_script"]
                    if "editor_state" in st.session_state:
                        del st.session_state["editor_state"]
                    st.success("✅ Guión descartado correctamente.")
                    st.rerun()

            s.title = st.text_input("Título", value=s.title)
            s.hook = st.text_area("Gancho Inicial (0-3s)", value=s.hook, height=70)


            st.markdown(f"**Escenas del Video ({len(s.scenes)} escenas):**")

            # Botones para añadir o quitar escenas
            btn_add_col, btn_del_col = st.columns(2)
            with btn_add_col:
                if st.button("➕ Añadir Escena"):
                    new_id = len(s.scenes) + 1
                    s.scenes.append(Scene(
                        scene_id=new_id,
                        text="Texto de la nueva escena explicativa.",
                        image_prompt=f"Cinematic ultra detailed shot related to {s.title}, photorealistic 8k",
                        keywords=["nuevo", "detalle"],
                        media_type="ai_image"
                    ))
                    st.rerun()
            with btn_del_col:
                if len(s.scenes) > 1 and st.button("🗑️ Eliminar Última Escena"):
                    s.scenes.pop()
                    st.rerun()

            cam_names = {
                "fast_punch_zoom": "⚡ Punch Zoom (Impacto)",
                "slow_dolly_in": "🔍 Dolly In (Intriga)",
                "slow_pull_out": "🔭 Pull Out (Revelación)",
                "pan_left": "⬅️ Paneo Izquierda",
                "pan_right": "➡️ Paneo Derecha",
                "camera_shake": "💥 Shake (Cámara en Mano)",
                "static_cinematic": "📐 Estático (Máxima Nitidez)"
            }
            trans_names = {
                "smash_cut": "✂️ Smash Cut (Corte Seco)",
                "crossfade": "🌫️ Disolvencia Suave",
                "whip_pan_right": "⚡ Whip Pan Derecha",
                "whip_pan_left": "⚡ Whip Pan Izquierda",
                "flash_white": "💡 Flash Blanco",
                "glitch": "👾 Glitch Digital",
                "zoom_in": "🌀 Zoom In Túnel"
            }
            sfx_names = {
                "impact_boom": "💥 Impact Boom (Golpe)",
                "whoosh_fast": "💨 Whoosh Rápido",
                "cinematic_riser": "📈 Riser de Tensión",
                "sub_bass_drop": "🌊 Sub-Bass Drop",
                "glitch_sfx": "⚡ Glitch Sonoro",
                "none": "🔇 Sin SFX (Silencio)"
            }

            for i, scene in enumerate(s.scenes):
                curr_type = getattr(scene, "media_type", "ai_image")
                curr_cam = getattr(scene, "camera_motion", "fast_punch_zoom" if i == 0 else "slow_dolly_in")
                curr_trans = getattr(scene, "transition_out", "whip_pan_right" if i % 2 == 0 else "crossfade")
                curr_sfx = getattr(scene, "sfx_cue", "impact_boom" if i == 0 else "whoosh_fast")

                with st.expander(f"🎬 Escena {i+1} ({len(scene.text.split())} palabras) — [{curr_type.upper()}] | {cam_names.get(curr_cam, curr_cam)} | {sfx_names.get(curr_sfx, curr_sfx)}", expanded=True):
                    edited_text = st.text_area(f"Texto hablado {i+1}", value=scene.text, key=f"scene_text_{i}", height=70)
                    if edited_text != scene.text:
                        scene.broll = []  # las búsquedas por frase eran del texto anterior; manda la búsqueda principal
                    scene.text = edited_text

                    m_idx = 0 if curr_type == "ai_image" else (1 if curr_type == "pexels_video" else 2)
                    m_choice = st.radio(
                        f"Motor Visual de la Escena {i+1}:",
                        options=["ai_image", "pexels_video", "3d_parallax"],
                        index=m_idx,
                        format_func=lambda x: "🖼️ Imagen IA (Flux)" if x == "ai_image" else ("📹 Video de Stock (Pexels 4K)" if x == "pexels_video" else "🌀 Animación 3D Parallax"),
                        key=f"scene_mtype_{i}",
                        horizontal=True
                    )
                    scene.media_type = m_choice

                    if m_choice == "pexels_video":
                        shown_query = getattr(scene, "pexels_query", "") or (scene.keywords[0] if scene.keywords else "cinematic 4k")
                        edited_query = st.text_input(
                            f"🔍 Búsqueda Pexels Video (Inglés) {i+1}",
                            value=shown_query,
                            key=f"scene_pexquery_{i}"
                        )
                        if edited_query != shown_query:
                            scene.broll = []
                        scene.pexels_query = edited_query

                    # Controles de dirección cinematográfica de la escena
                    d_c1, d_c2, d_c3 = st.columns(3)
                    with d_c1:
                        cam_keys = list(cam_names.keys())
                        c_idx = cam_keys.index(curr_cam) if curr_cam in cam_keys else 0
                        scene.camera_motion = st.selectbox(
                            f"🎥 Movimiento de Cámara {i+1}",
                            options=cam_keys,
                            index=c_idx,
                            format_func=lambda x: cam_names.get(x, x),
                            key=f"scene_cam_{i}"
                        )
                    with d_c2:
                        trans_keys = list(trans_names.keys())
                        t_idx = trans_keys.index(curr_trans) if curr_trans in trans_keys else 0
                        scene.transition_out = st.selectbox(
                            f"🎬 Transición Saliente {i+1}",
                            options=trans_keys,
                            index=t_idx,
                            format_func=lambda x: trans_names.get(x, x),
                            key=f"scene_trans_{i}"
                        )
                    with d_c3:
                        sfx_keys = list(sfx_names.keys())
                        s_idx = sfx_keys.index(curr_sfx) if curr_sfx in sfx_keys else 0
                        scene.sfx_cue = st.selectbox(
                            f"🔊 Efecto Sonoro (SFX) {i+1}",
                            options=sfx_keys,
                            index=s_idx,
                            format_func=lambda x: sfx_names.get(x, x),
                            key=f"scene_sfx_{i}"
                        )

            # Reconstruir full_script_text (Sin CTA)
            s.full_script_text = f"{s.hook} " + " ".join(sc.text for sc in s.scenes)
            st.session_state["current_script"] = s

            st.markdown("---")
            st.markdown("#### 3. Fabricar Video")

            if st.button("🎬 FABRICAR VIDEO COMPLETO AHORA", type="primary", use_container_width=True):
                progress_bar = st.progress(0)
                status_text = st.empty()

                def update_ui_progress(pct: int, msg: str):
                    progress_bar.progress(pct)
                    status_text.info(f"⏳ **{pct}%** - {msg}")

                try:
                    results = pipeline.generate_multilang(
                        languages=["es", "en"] if also_english else ["es"],
                        platform=selected_platform,
                        topic=s.title,
                        niche=selected_niche_key,
                        voice=selected_voice_id,
                        voice_rate=voice_rate_choice,
                        target_duration=target_duration,
                        scene_count=len(s.scenes),
                        aspect_ratio=aspect_ratio,
                        enable_subtitles=enable_subtitles,
                        subtitle_style=selected_sub_style,
                        subtitle_position=sub_position,

                        subtitle_font_size=font_size,
                        words_per_line=words_per_line,
                        music_volume=music_vol,
                        zoom_intensity=zoom_intensity,
                        custom_script=s,
                        progress_callback=update_ui_progress
                    )
                    st.session_state["last_results"] = [r.publication_package for r in results]
                    st.session_state["last_video"] = results[0].video_path
                    show_generation_result(results[0], status_text)
                    # Solo el primer idioma: la versión en inglés iría a las mismas cuentas.
                    run_auto_publish(results[0].publication_package, st.container())
                except Exception as e:
                    status_text.error(f"❌ Error al fabricar el Video: {e}")



    with col_right:
        st.markdown("#### 📱 Previsualización del Video")
        packages = [p for p in st.session_state.get("last_results", []) if p and os.path.exists(p)]
        if packages:
            render_publication_panel(packages)
        elif "last_video" in st.session_state and os.path.exists(st.session_state["last_video"]):
            vid_path = st.session_state["last_video"]
            st.video(vid_path)
            with open(vid_path, "rb") as vf:
                st.download_button("⬇️ Descargar MP4", data=vf.read(), file_name=os.path.basename(vid_path),
                                   mime="video/mp4", use_container_width=True)
        else:
            st.info("Genera un Reel para previsualizar el resultado final en formato vertical 9:16 con subtítulos dinámicos.")

# =======================================================
# TAB EDITOR: ESTUDIO DE EDICIÓN IA (CAPCUT / INSHOT)
# =======================================================
with tab_editor:
    st.markdown("### 🎬 Estudio de Edición IA & Copilot (Tipo CapCut / InShot)")
    st.markdown("Edita y personaliza tu proyecto usando **comandos de texto en lenguaje natural** o ajustando la **línea de tiempo multi-pista interactiva**.")

    # Asegurar que siempre haya un proyecto activo para que la línea de tiempo y herramientas siempre tengan contenido
    if "current_script" not in st.session_state or st.session_state["current_script"] is None:
        st.session_state["current_script"] = pipeline.script_gen._generate_offline(
            topic="",
            niche=selected_niche_key,
            target_duration=30,
            scene_count=3
        )

    cur_s: ReelScript = st.session_state["current_script"]

    ed_top1, ed_top2, ed_top3 = st.columns([1.8, 1, 1])
    with ed_top1:
        st.caption(f"🎬 **Proyecto Activo en Editor:** `{cur_s.title}` *(Nicho: {cur_s.niche})*")
    with ed_top2:
        if st.button("➕ Proyecto en Blanco", key="btn_ed_blank", use_container_width=True):
            st.session_state["current_script"] = ReelScript(
                title="Nuevo Proyecto en Blanco",
                niche=selected_niche_key,
                hook="Escribe tu gancho de 0 a 3 segundos aquí...",
                scenes=[
                    Scene(
                        scene_id=1,
                        text="Texto de la primera escena...",
                        image_prompt="Cinematic shot, vertical 9:16, 8k",
                        keywords=["inicio"],
                        media_type="pexels_video",
                        pexels_query="cinematic 4k",
                        camera_motion="fast_punch_zoom",
                        transition_out="whip_pan_right",
                        visual_vfx="deep_vignette",
                        sfx_cue="impact_boom",
                        subtitle_mode="single_word_punch",
                        accent_color="&H0000E5FF&"
                    )
                ],
                cta="",
                full_script_text=""
            )
            if "editor_state" in st.session_state:
                del st.session_state["editor_state"]
            st.rerun()
    with ed_top3:
        if st.button("📂 Plantilla del Nicho", key="btn_ed_niche_tpl", use_container_width=True):
            st.session_state["current_script"] = pipeline.script_gen._generate_offline(
                topic="",
                niche=selected_niche_key,
                target_duration=30,
                scene_count=3
            )
            if "editor_state" in st.session_state:
                del st.session_state["editor_state"]
            st.rerun()

    # Inicializar estado completo del proyecto si no existe
    if "editor_state" not in st.session_state:
        st.session_state["editor_state"] = {
            "title": cur_s.title,
            "hook": cur_s.hook,
            "scenes": [
                {
                    "scene_id": sc.scene_id,
                    "text": sc.text,
                    "media_type": getattr(sc, "media_type", "ai_image"),
                    "pexels_query": getattr(sc, "pexels_query", ""),
                    "image_prompt": sc.image_prompt,
                    "keywords": sc.keywords
                } for sc in cur_s.scenes
            ],
            "voice": "es-MX-JorgeNeural",
            "voice_rate": "+5%",
            "subtitle_style": "yellow_pop",
            "subtitle_position": "bottom",
            "words_per_line": 3,
            "aspect_ratio": "9:16",
            "zoom_intensity": 1.0,
            "music_volume": 0.12,
            "target_duration": 30
        }

    proj = st.session_state["editor_state"]


    # Sincronizar escenas con cur_s
    proj["title"] = cur_s.title
    proj["hook"] = cur_s.hook

    copilot = AIEditorCopilot()

    col_chat, col_timeline = st.columns([1.1, 1.4])

    # -------------------------------------------------------------
    # COLUMNA IZQUIERDA: ASISTENTE COPILOT POR COMANDOS DE TEXTO
    # -------------------------------------------------------------
    with col_chat:
        ed_h_col1, ed_h_col2 = st.columns([1.5, 1])
        with ed_h_col1:
            st.markdown("#### 🤖 Asistente Copilot de Edición")
        with ed_h_col2:
            if st.button("🗑️ Descartar / Reiniciar", help="Descarta el guión actual de la memoria y reinicia el editor"):
                if "current_script" in st.session_state:
                    del st.session_state["current_script"]
                if "editor_state" in st.session_state:
                    del st.session_state["editor_state"]
                if "last_copilot_explanation" in st.session_state:
                    del st.session_state["last_copilot_explanation"]
                st.success("✅ Proyecto reiniciado.")
                st.rerun()

        st.markdown("Escribe lo que deseas modificar en lenguaje natural y la IA ajustará el proyecto de inmediato:")

        user_edit_cmd = st.text_area(
            "💬 Comando de Edición (Lenguaje Natural):",
            placeholder="Ejemplo: Cambia la escena 2 por un video de Pexels de olas del mar, pon los subtítulos en verde neón en el centro y acelera la voz un 10%",
            height=100
        )


        st.markdown("💡 **Comandos Rápidos de Ejemplo:**")
        chip_c1, chip_c2 = st.columns(2)
        with chip_c1:
            if st.button("🌀 Aplicar 3D Parallax a Todo", use_container_width=True):
                user_edit_cmd = "Aplica efecto 3D Parallax a todas las escenas de imagen"
            if st.button("🔴 Subtítulos Rojo Fuego Arriba", use_container_width=True):
                user_edit_cmd = "Cambia los subtítulos a estilo rojo fuego y colócalos arriba"
        with chip_c2:
            if st.button("📹 Usar Videos Pexels de Stock", use_container_width=True):
                user_edit_cmd = "Configura las escenas para buscar videos reales de archivo en Pexels"
            if st.button("📺 Cambiar a YouTube 16:9", use_container_width=True):
                user_edit_cmd = "Cambia el formato a horizontal 16:9 para YouTube con subtítulos abajo"

        if st.button("✨ Ejecutar Edición con IA", type="primary", use_container_width=True):
            if user_edit_cmd and user_edit_cmd.strip():
                with st.spinner("El Copilot de IA está interpretando tus instrucciones y editando el proyecto..."):
                    updated_proj, explanation = copilot.edit_project(proj, user_edit_cmd)
                    st.session_state["editor_state"] = updated_proj

                    # Sincronizar de vuelta a ReelScript
                    new_scenes = []
                    for sc_data in updated_proj.get("scenes", []):
                        new_scenes.append(Scene(
                            scene_id=sc_data.get("scene_id", len(new_scenes)+1),
                            text=sc_data.get("text", ""),
                            image_prompt=sc_data.get("image_prompt", ""),
                            keywords=sc_data.get("keywords", []),
                            media_type=sc_data.get("media_type", "ai_image"),
                            pexels_query=sc_data.get("pexels_query", "")
                        ))

                    cur_s.title = updated_proj.get("title", cur_s.title)
                    cur_s.hook = updated_proj.get("hook", cur_s.hook)
                    cur_s.cta = ""
                    cur_s.scenes = new_scenes
                    cur_s.full_script_text = f"{cur_s.hook} " + " ".join(s.text for s in new_scenes)
                    st.session_state["current_script"] = cur_s

                    st.session_state["last_copilot_explanation"] = explanation
                    st.rerun()


        if "last_copilot_explanation" in st.session_state:
            st.success(f"**Resultado de la Edición IA:**\n\n{st.session_state['last_copilot_explanation']}")

    # -------------------------------------------------------------
    # COLUMNA DERECHA: LÍNEA DE TIEMPO INTERACTIVA (MULTI-TRACK)
    # -------------------------------------------------------------
    with col_timeline:
        st.markdown("#### 🎞️ Línea de Tiempo Multi-Pista")

        # PISTA 1: VISUALES / ESCENAS
        st.markdown("##### 🎬 Pista 1: Visuales & Escenas")
        for idx, sc in enumerate(cur_s.scenes):
            m_type = getattr(sc, "media_type", "ai_image")
            badge_icon = "🖼️ IA Flux" if m_type == "ai_image" else ("📹 Pexels 4K" if m_type == "pexels_video" else "🌀 3D Parallax")

            with st.container():
                st.markdown(f"""
                <div style="background-color: #1E222D; border: 1px solid #374151; border-radius: 8px; padding: 10px; margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: bold; color: #60A5FA;">Escena {idx+1}</span>
                        <span style="background: #2563EB; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem;">{badge_icon}</span>
                    </div>
                    <div style="color: #D1D5DB; font-size: 0.9rem; margin-top: 4px;">"{sc.text}"</div>
                </div>
                """, unsafe_allow_html=True)

        # PISTA 2: SUBTÍTULOS & ESTILO
        st.markdown("##### 🔤 Pista 2: Subtítulos Dinámicos")
        sub_c1, sub_c2, sub_c3 = st.columns(3)
        with sub_c1:
            sub_choices = ["yellow_pop", "green_neon", "cyan_electric", "red_fire", "white_pure", "none"]
            curr_ed_sub = proj.get("subtitle_style", "yellow_pop")
            if curr_ed_sub not in sub_choices:
                curr_ed_sub = "yellow_pop"
            proj["subtitle_style"] = st.selectbox(
                "Color / Estilo",
                sub_choices,
                index=sub_choices.index(curr_ed_sub),
                format_func=lambda x: "🚫 Desactivados (Sin texto)" if x == "none" else x,
                key="ed_sub_style"
            )
        with sub_c2:
            proj["subtitle_position"] = st.selectbox(
                "Posición",
                ["bottom", "center", "top"],
                index=["bottom", "center", "top"].index(proj.get("subtitle_position", "bottom")),
                key="ed_sub_pos"
            )
        with sub_c3:
            proj["words_per_line"] = st.slider(
                "Palabras / Pop",
                min_value=1, max_value=5,
                value=int(proj.get("words_per_line", 3)),
                key="ed_sub_wpl"
            )

        # PISTA 3: AUDIO & VOZ
        st.markdown("##### 🎙️ Pista 3: Audio & Locución")
        aud_c1, aud_c2, aud_c3 = st.columns(3)
        with aud_c1:
            proj["voice_rate"] = st.select_slider(
                "Velocidad Voz",
                ["-10%", "+0%", "+5%", "+12%", "+20%"],
                value=proj.get("voice_rate", "+5%"),
                key="ed_voice_rate"
            )
        with aud_c2:
            proj["music_volume"] = st.slider(
                "Volumen Música",
                min_value=0.0, max_value=0.35,
                value=float(proj.get("music_volume", 0.12)),
                step=0.02,
                key="ed_music_vol"
            )
        with aud_c3:
            proj["aspect_ratio"] = st.selectbox(
                "Formato Salida",
                ["9:16", "1:1", "16:9"],
                index=["9:16", "1:1", "16:9"].index(proj.get("aspect_ratio", "9:16")),
                key="ed_aspect"
            )

        st.markdown("---")
        # BOTÓN RENDERIZAR DESDE EL EDITOR
        if st.button("🚀 EXPORTAR / RENDERIZAR VIDEO EDITADO", type="primary", use_container_width=True):
            pbar = st.progress(0)
            st_text = st.empty()

            def update_ed_progress(pct: int, msg: str):
                pbar.progress(pct)
                st_text.info(f"⏳ **{pct}%** - {msg}")

            try:
                is_sub_enabled = (proj.get("subtitle_style") != "none")
                res = pipeline.generate_reel(
                    topic=cur_s.title,
                    niche=cur_s.niche or "curiosidades",
                    voice=selected_voice_id,
                    voice_rate=proj.get("voice_rate", "+5%"),
                    target_duration=proj.get("target_duration", 30),
                    scene_count=len(cur_s.scenes),
                    aspect_ratio=proj.get("aspect_ratio", "9:16"),
                    enable_subtitles=is_sub_enabled,
                    subtitle_style=proj.get("subtitle_style", "yellow_pop"),
                    subtitle_position=proj.get("subtitle_position", "bottom"),
                    words_per_line=int(proj.get("words_per_line", 3)),
                    music_volume=float(proj.get("music_volume", 0.12)),
                    zoom_intensity=float(proj.get("zoom_intensity", 1.0)),
                    custom_script=cur_s,
                    progress_callback=update_ed_progress
                )
                st.session_state["last_video"] = res.video_path
                st.session_state["last_results"] = [res.publication_package]  # panel de publicación de la pestaña Crear
                show_generation_result(res, st_text)
                run_auto_publish(res.publication_package, st.container())
            except Exception as e:
                st_text.error(f"❌ Error al exportar: {e}")


# ==========================================
# TAB POST: IMAGEN CON TITULAR
# ==========================================
with tab_post:
    post_left, post_right = st.columns([1, 1])
    with post_left:
        st.markdown("### 🖼️ Post de imagen para Instagram y Facebook")
        st.caption("Un titular grande sobre la imagen y el texto completo en la descripción. "
                   "Se publica solo en tus cuentas conectadas al terminar.")
        post_niche = st.selectbox("Nicho", options=list(all_niches.keys()),
                                  format_func=lambda k: all_niches[k].get("name", k), key="post_niche")
        post_topic = st.text_input("Tema (o 'aleatorio')", value="aleatorio", key="post_topic")
        if st.button("🖼️ CREAR POST", type="primary", use_container_width=True):
            post_bar = st.progress(0)
            post_status = st.empty()

            def update_post_progress(pct: int, msg: str):
                post_bar.progress(pct)
                post_status.info(f"⏳ **{pct}%** - {msg}")

            try:
                st.session_state["last_post"] = pipeline.create_post(post_topic, post_niche, progress_callback=update_post_progress)
                run_auto_publish(st.session_state["last_post"], st.container())
            except Exception as e:
                post_status.error(f"❌ Error al crear el post: {e}")

    with post_right:
        post_package_path = st.session_state.get("last_post")
        if post_package_path and os.path.exists(post_package_path):
            import json
            with open(post_package_path, "r", encoding="utf-8") as post_file:
                post_package = json.load(post_file)
            st.image(post_package["image"], use_container_width=True)
            with open(post_package["image"], "rb") as image_file:
                st.download_button("⬇️ Descargar imagen", data=image_file.read(),
                                   file_name=os.path.basename(os.path.dirname(post_package["image"])) + ".jpg",
                                   mime="image/jpeg", use_container_width=True)
            st.markdown("**📋 Texto para publicar:**")
            st.code(post_package.get("caption_completo", ""), language=None, wrap_lines=True)
            if post_package.get("fuentes_y_creditos"):
                with st.expander("📚 Fuentes"):
                    st.code(post_package["fuentes_y_creditos"], language=None, wrap_lines=True)
            st.caption(f"Texto: {post_package.get('script_source')}")
            render_publication_status(post_package_path, post_package)
        else:
            st.info("Crea un post para verlo aquí.")

# ==========================================
# TAB 2: FÁBRICA EN LOTE (BATCH)
# ==========================================

with tab2:
    st.markdown("### 🏭 Modo Fábrica: Producción Masiva de Reels")
    st.markdown("Genera una serie completa de videos automáticamente aplicando las skills de Creatividad y Dirección.")

    niche_keys = list(all_niches.keys())
    niche_labels = {k: all_niches[k].get("name", k) for k in niche_keys}

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        batch_niche = st.selectbox(
            "Nicho para el Lote",
            options=niche_keys,
            format_func=lambda x: niche_labels.get(x, x),
            key="batch_niche"
        )
        batch_preset = st.radio(
            "Origen de los temas:",
            ["Ingresar lista manual", "Generar 5 ideas automáticas"]
        )

    with col_b2:
        default_batch_topics = """3 Lugares del Planeta donde está Prohibido Entrar
El Misterio de la Civilización de la Atlántida
Por qué los Pulpos tienen 3 Corazones y Sangre Azul
El Hábito de 5 Minutos que Duplica tu Productividad
Qué pasaría si la Luna Desapareciera Mañana"""

        if batch_preset == "Ingresar lista manual":
            batch_text = st.text_area(
                "Lista de Temas (1 por línea):",
                value=default_batch_topics,
                height=160
            )
            topics_list = [line.strip() for line in batch_text.split("\n") if line.strip()]
        else:
            if st.button("💡 Proponer 5 temas con IA", key="batch_suggest"):
                with st.spinner("Buscando temas concretos para el nicho..."):
                    st.session_state["batch_auto_topics"] = pipeline.script_gen.suggest_topics(batch_niche, 5)
            topics_list = st.session_state.get("batch_auto_topics", [])
            if topics_list:
                st.write("Temas asignados:", topics_list)
            else:
                st.caption("Pulsa el botón para proponer temas.")

        batch_duration = st.select_slider("Duración de cada video (s)", options=[15, 30, 45, 60, 90], value=30, key="batch_duration")
        batch_name = st.text_input(
            "Nombre del lote (repetir el nombre reanuda y salta los videos ya hechos)",
            value=st.session_state.get("batch_name_default", f"lote_{datetime.datetime.now():%Y%m%d_%H%M}"),
            key="batch_name",
        )

    st.markdown(f"**Total de Reels a producir:** `{len(topics_list)}`")

    if st.button("⚡ INICIAR PRODUCCIÓN EN MASA", type="primary"):
        if not topics_list:
            st.warning("Ingresa al menos un tema para procesar.")
        else:
            from src.batch import BatchItem, run_batch
            batch_progress = st.progress(0)
            batch_status = st.empty()
            batch_status.markdown(f"🔄 Produciendo {len(topics_list)} videos del lote `{batch_name}`...")
            items = [BatchItem(topic=t, niche=batch_niche, duration=batch_duration) for t in topics_list]

            def batch_cb(outcome, done, total):
                batch_progress.progress(int(done / total * 100))
                icon = {"completed": "✅", "degraded": "⚠️", "skipped": "⏭️", "failed": "❌"}.get(outcome.status, "•")
                detail = f" — {outcome.error[:200]}" if outcome.error else ""
                st.write(f"{icon} **{outcome.topic}** → {outcome.status} ({outcome.seconds}s){detail}")

            outcomes = run_batch(
                items, batch_name, workers=1,
                options={"voice": selected_voice_id, "subtitle_style": selected_sub_style},
                on_progress=batch_cb,
            )
            ok = [o for o in outcomes if o.status != "failed"]
            batch_status.success(f"🎉 Lote finalizado: {len(ok)}/{len(outcomes)} videos listos. Informe en `output/lotes/{batch_name}.csv`.")
            st.session_state["batch_results"] = [o.video_path for o in ok]

# ==========================================
# TAB: GESTOR DE NICHOS
# ==========================================
with tab_niches:
    st.markdown("### 🎯 Gestor de Nichos y Dirección Creativa")
    st.markdown("Configura los nichos activos, su tono narrativo y el estilo visual de los prompts cinematográficos.")

    n_col1, n_col2 = st.columns([1.2, 0.8])

    with n_col1:
        st.markdown("#### 📚 Nichos Disponibles Actualmente")
        for key, data in all_niches.items():
            with st.expander(f"{data.get('name', key)} (`{key}`)", expanded=False):
                st.markdown(f"**Descripción:** {data.get('description', '')}")
                st.markdown(f"**Tono Narrativo:** `{data.get('tone', '')}`")
                st.markdown(f"**Estilo Visual Cinematográfico:** `{data.get('visual_style', '')}`")
                st.markdown(f"**Voz Recomendada:** `{data.get('recommended_voice', '')}`")

    with n_col2:
        st.markdown("#### ➕ Crear Nuevo Nicho Personalizado")
        with st.form("new_niche_form"):
            new_n_key = st.text_input("Identificador Único (sin espacios)", value="anime_manga", help="ej: futbol_datos, gaming_clips, cocina_rapida")
            new_n_name = st.text_input("Nombre con Emoji", value="⚔️ Anime & Manga Shonen", help="ej: ⚽ Historias del Fútbol Mundial")
            new_n_desc = st.text_area("Descripción del Nicho", value="Misterios, teorías y momentos épicos del anime.")
            new_n_tone = st.text_input("Tono Narrativo", value="Épico, hiper-enérgico, con pausas de gran emoción.")
            new_n_visual = st.text_area(
                "Estilo Visual para Prompts IA",
                value="dynamic anime cinematic art, Makoto Shinkai aesthetic, dramatic lighting, vivid colors, vertical 9:16 wallpaper, 8k render"
            )
            new_n_voice = st.selectbox(
                "Voz Recomendada",
                options=[v["id"] for v in voices],
                index=0
            )

            submit_niche = st.form_submit_button("💾 Guardar Nuevo Nicho", type="primary")

            if submit_niche:
                if new_n_key and new_n_name:
                    niche_mgr.add_or_update_niche(
                        key=new_n_key,
                        name=new_n_name,
                        description=new_n_desc,
                        tone=new_n_tone,
                        visual_style=new_n_visual,
                        recommended_voice=new_n_voice
                    )
                    st.success(f"✅ ¡Nicho '{new_n_name}' guardado correctamente! Recarga la página para verlo en la lista.")
                    st.rerun()
                else:
                    st.error("Por favor completa al menos el identificador y el nombre.")


# ==========================================
# TAB 3: GALERÍA DE REELS
# ==========================================
with tab3:
    gal_col1, gal_col2 = st.columns([1.6, 1])
    with gal_col1:
        st.markdown("### 📁 Galería de Reels Generados")
    with gal_col2:
        if st.button("🧹 Limpiar Borradores Huérfanos", help="Elimina carpetas temporales de guiones o intentos donde no se completó el video"):
            import shutil
            cleaned_count = 0
            if os.path.exists("output"):
                for item in os.listdir("output"):
                    item_path = os.path.join("output", item)
                    if os.path.isdir(item_path) and item.startswith("reel_"):
                        mp4_match = os.path.join("output", f"{item}.mp4")
                        if not os.path.exists(mp4_match):
                            shutil.rmtree(item_path, ignore_errors=True)
                            cleaned_count += 1
            if os.path.exists("output/temp_media"):
                for t_item in os.listdir("output/temp_media"):
                    t_path = os.path.join("output/temp_media", t_item)
                    if os.path.isdir(t_path):
                        mp4_match = os.path.join("output", f"{t_item}.mp4")
                        if not os.path.exists(mp4_match):
                            shutil.rmtree(t_path, ignore_errors=True)
                            cleaned_count += 1
            st.success(f"✅ Se eliminaron {cleaned_count} carpetas temporales de borradores huérfanos.")
            st.rerun()

    video_files = glob.glob("output/*.mp4")
    video_files.sort(key=os.path.getmtime, reverse=True)


    if not video_files:
        st.info("Aún no hay videos generados en la carpeta `output/`.")
    else:
        st.caption(f"Mostrando {len(video_files)} videos renderizados:")

        # Grid de 3 columnas para ver los videos
        cols = st.columns(3)
        for i, v_path in enumerate(video_files):
            col = cols[i % 3]
            with col:
                st.markdown(f"**{os.path.basename(v_path)}**")
                file_size_mb = os.path.getsize(v_path) / (1024 * 1024)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(v_path)).strftime("%d/%m/%Y %H:%M")
                st.caption(f"📅 {mtime} | 💾 {file_size_mb:.1f} MB")
                st.video(v_path)

# ==========================================
# TAB 4: CONFIGURACIÓN & API KEYS
# ==========================================
with tab4:
    st.markdown("### ⚙️ Configuración y Claves de API")
    st.info("💡 **Nota:** La fábrica funciona de manera 100% autónoma y gratuita con Edge-TTS y Pollinations Flux sin necesidad de API keys. Añade claves si deseas usar modelos avanzados de OpenAI, Gemini o cualquier otra IA.")

    st.markdown("#### 🌐 Proveedores Tradicionales")
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        new_gemini = st.text_input("Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password")
        new_openai = st.text_input("OpenAI API Key", value=os.getenv("OPENAI_API_KEY", ""), type="password")
    with col_k2:
        new_groq = st.text_input("Groq API Key", value=os.getenv("GROQ_API_KEY", ""), type="password")
        new_pexels = st.text_input("Pexels API Key", value=os.getenv("PEXELS_API_KEY", ""), type="password")
        new_pollinations = st.text_input("Pollinations Token (opcional, imágenes en paralelo)", value=os.getenv("POLLINATIONS_TOKEN", ""), type="password")

    st.markdown("---")
    st.markdown("#### ⚡ Conectar CUALQUIER Otra IA (OpenRouter, DeepSeek, Mistral, Ollama, etc.)")
    st.caption("Cualquier proveedor compatible con el estándar OpenAI puede conectarse aquí especificando su URL base y modelo:")

    preset_choice = st.selectbox(
        "💡 Cargar plantilla rápida de configuración:",
        [
            "Personalizado / Manual",
            "OpenRouter - Llama 3.3 70B (Gratis)",
            "OpenRouter - DeepSeek R1 (Gratis)",
            "OpenRouter - Gemini 2.0 Flash (Gratis)",
            "DeepSeek Oficial (Directo)",
            "Ollama (Local en tu PC)"
        ]
    )

    default_base_url = os.getenv("CUSTOM_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    default_model = os.getenv("CUSTOM_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

    if preset_choice == "OpenRouter - Llama 3.3 70B (Gratis)":
        default_base_url = "https://openrouter.ai/api/v1"
        default_model = "meta-llama/llama-3.3-70b-instruct:free"
    elif preset_choice == "OpenRouter - DeepSeek R1 (Gratis)":
        default_base_url = "https://openrouter.ai/api/v1"
        default_model = "deepseek/deepseek-r1:free"
    elif preset_choice == "OpenRouter - Gemini 2.0 Flash (Gratis)":
        default_base_url = "https://openrouter.ai/api/v1"
        default_model = "google/gemini-2.0-flash-exp:free"
    elif preset_choice == "DeepSeek Oficial (Directo)":
        default_base_url = "https://api.deepseek.com/v1"
        default_model = "deepseek-chat"
    elif preset_choice == "Ollama (Local en tu PC)":
        default_base_url = "http://localhost:11434/v1"
        default_model = "llama3"

    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        new_custom_url = st.text_input(
            "Base URL",
            value=default_base_url,
            help="Ejemplo: https://openrouter.ai/api/v1, https://api.deepseek.com/v1, http://localhost:11434/v1"
        )
    with col_c2:
        new_custom_key = st.text_input(
            "API Key de la IA",
            value=os.getenv("CUSTOM_LLM_API_KEY", ""),
            type="password",
            help="Tu clave obtenida en OpenRouter (comienza con 'sk-or-...'), DeepSeek, etc."
        )
    with col_c3:
        new_custom_model = st.text_input(
            "Nombre del Modelo (con prefijo en OpenRouter)",
            value=default_model,
            help="Ejemplo: meta-llama/llama-3.3-70b-instruct:free, deepseek/deepseek-r1:free, deepseek-chat"
        )


    if st.button("💾 Guardar Configuración", type="primary"):
        updates = {
            "GEMINI_API_KEY": new_gemini,
            "OPENAI_API_KEY": new_openai,
            "GROQ_API_KEY": new_groq,
            "PEXELS_API_KEY": new_pexels,
            "POLLINATIONS_TOKEN": new_pollinations,
            "CUSTOM_LLM_BASE_URL": new_custom_url,
            "CUSTOM_LLM_API_KEY": new_custom_key,
            "CUSTOM_LLM_MODEL": new_custom_model,
        }
        save_env(updates)
        st.success("✅ Configuración guardada en archivo `.env` correctamente.")

    st.markdown("---")
    st.markdown("#### 🧪 Probar y Diagnosticar Conectividad de APIs")
    st.caption("Verifica al instante si tus claves de API son válidas y están respondiendo correctamente:")

    from src.core.api_checker import APIChecker

    col_t1, col_t2, col_t3 = st.columns(3)

    with col_t1:
        if st.button("🧪 Probar Gemini"):
            with st.spinner("Probando Google Gemini..."):
                r = APIChecker.test_gemini(new_gemini or os.getenv("GEMINI_API_KEY", ""))
                if r["status"]:
                    st.success(f"✅ {r['message']} ({r['latency']}s)")
                else:
                    st.error(f"❌ {r['message']}")

        if st.button("🧪 Probar Groq"):
            with st.spinner("Probando Groq Cloud..."):
                r = APIChecker.test_groq(new_groq or os.getenv("GROQ_API_KEY", ""))
                if r["status"]:
                    st.success(f"✅ {r['message']} ({r['latency']}s)")
                else:
                    st.error(f"❌ {r['message']}")

    with col_t2:
        if st.button("🧪 Probar OpenAI"):
            with st.spinner("Probando OpenAI..."):
                r = APIChecker.test_openai(new_openai or os.getenv("OPENAI_API_KEY", ""))
                if r["status"]:
                    st.success(f"✅ {r['message']} ({r['latency']}s)")
                else:
                    st.error(f"❌ {r['message']}")

        if st.button("🧪 Probar IA Personalizada"):
            with st.spinner("Probando IA Personalizada..."):
                r = APIChecker.test_custom_llm(
                    new_custom_url or os.getenv("CUSTOM_LLM_BASE_URL", ""),
                    new_custom_key or os.getenv("CUSTOM_LLM_API_KEY", ""),
                    new_custom_model or os.getenv("CUSTOM_LLM_MODEL", "deepseek-chat")
                )
                if r["status"]:
                    st.success(f"✅ {r['message']} ({r['latency']}s)")
                else:
                    st.error(f"❌ {r['message']}")

    with col_t3:
        if st.button("🧪 Probar Pexels Stock"):
            with st.spinner("Probando Pexels API..."):
                r = APIChecker.test_pexels(new_pexels or os.getenv("PEXELS_API_KEY", ""))
                if r["status"]:
                    st.success(f"✅ {r['message']} ({r['latency']}s)")
                else:
                    st.error(f"❌ {r['message']}")

        if st.button("🧪 Probar Voces e Imágenes"):
            with st.spinner("Probando servicios gratuitos..."):
                r_tts = APIChecker.test_edge_tts()
                r_img = APIChecker.test_pollinations()
                if r_tts["status"]:
                    st.success(f"✅ Voces: {r_tts['message']}")
                else:
                    st.error(f"❌ Voces: {r_tts['message']}")
                if r_img["status"]:
                    st.success(f"✅ Imágenes: {r_img['message']}")
                else:
                    st.error(f"❌ Imágenes: {r_img['message']}")

    st.markdown("---")
    st.markdown("#### 📤 Conectar Redes Sociales (publicación automática)")
    pub_settings = pipeline.config.get("publishing", {})
    connected_now = publisher.connected_platforms()
    st.caption(
        f"Conectadas: {', '.join(NETWORK_NAMES[p] for p in connected_now) or 'ninguna'} · "
        f"Publicación automática al terminar cada video: {'🟢 activada' if pub_settings.get('auto') else '⚪ desactivada'} "
        "(se cambia en `config/config.yaml` → `publishing`)."
    )

    with st.expander("📸 Instagram + 📘 Facebook (una sola conexión con Meta)", expanded="instagram" not in connected_now):
        st.markdown(
            "1. Pasa tu Instagram a cuenta **Profesional** (Creador o Empresa) y vincúlala a tu **Página de Facebook**.\n"
            "2. En [developers.facebook.com](https://developers.facebook.com/apps) crea una app de tipo **Empresa** "
            "y añade el producto **Inicio de sesión con Facebook para empresas** e **Instagram**. Copia el *ID* y la *clave secreta* de la app.\n"
            "3. Abre el [Explorador de la API Graph](https://developers.facebook.com/tools/explorer), elige tu app y pulsa "
            "**Generate Access Token** con los permisos `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, "
            "`business_management`, `instagram_basic` e `instagram_content_publish`.\n"
            "4. Pega aquí los tres datos. La clave secreta solo se usa para obtener un token de página que no caduca; no se guarda."
        )
        meta_c1, meta_c2 = st.columns(2)
        with meta_c1:
            meta_app_id = st.text_input("ID de la app de Meta", key="meta_app_id")
            meta_secret = st.text_input("Clave secreta de la app", type="password", key="meta_secret")
        with meta_c2:
            meta_user_token = st.text_input("Token del Explorador de la API Graph", type="password", key="meta_user_token")
        if st.button("🔎 Buscar mis páginas", disabled=not (meta_app_id and meta_secret and meta_user_token)):
            try:
                st.session_state["meta_pages"] = publisher.meta_pages(meta_app_id.strip(), meta_secret.strip(), meta_user_token.strip())
                if not st.session_state["meta_pages"]:
                    st.warning("Ese token no tiene páginas. Revisa que concediste `pages_show_list` y elegiste tu página al generar el token.")
            except Exception as e:
                st.error(f"❌ {e}")
        meta_pages = st.session_state.get("meta_pages", [])
        if meta_pages:
            chosen = st.selectbox(
                "Página a conectar", meta_pages,
                format_func=lambda p: f"{p['name']} · Instagram: @{p['instagram_username']}" if p["instagram_id"] else f"{p['name']} · (sin Instagram vinculado)",
            )
            if st.button("🔗 Conectar esta página", type="primary"):
                save_env({"META_PAGE_ID": chosen["id"], "META_PAGE_TOKEN": chosen["token"], "INSTAGRAM_USER_ID": chosen["instagram_id"]})
                st.session_state.pop("meta_pages", None)
                st.success("✅ Meta conectada." + ("" if chosen["instagram_id"] else " Esta página no tiene Instagram vinculado: solo se publicará en Facebook."))
                st.rerun()
            if st.button("📰 Usarla como página de noticias"):
                save_env({"NEWS_PAGE_ID": chosen["id"], "NEWS_PAGE_TOKEN": chosen["token"]})
                st.session_state.pop("meta_pages", None)
                st.success(f"✅ {chosen['name']} será la página de noticias (`python run.py --news`).")
                st.rerun()

    with st.expander("▶️ YouTube Shorts", expanded="youtube" not in connected_now):
        st.markdown(
            "1. En [console.cloud.google.com](https://console.cloud.google.com) crea un proyecto y habilita **YouTube Data API v3**.\n"
            "2. En **Pantalla de consentimiento OAuth** elige *Externo*, añade tu correo y pulsa **Publicar app** "
            "(en modo *Prueba* la conexión caduca cada 7 días).\n"
            "3. En **Credenciales** crea un **ID de cliente OAuth** de tipo **App de escritorio** y descarga el JSON.\n"
            "4. Súbelo aquí y pulsa conectar: se abrirá el navegador para autorizar tu canal.\n\n"
            "⚠️ Google publica en **privado** los videos subidos por apps sin auditar. Para que salgan públicos, "
            "solicita la auditoría gratuita en el [formulario de YouTube API Services](https://support.google.com/youtube/contact/yt_api_form)."
        )
        secret_file = st.file_uploader("JSON del cliente OAuth", type=["json"], key="yt_secret")
        if secret_file is not None:
            with open(publisher.YOUTUBE_SECRET, "wb") as f:
                f.write(secret_file.getvalue())
        if st.button("🔗 Conectar canal de YouTube", disabled=not os.path.exists(publisher.YOUTUBE_SECRET)):
            try:
                with st.spinner("Autoriza el acceso en la ventana del navegador que se abrió..."):
                    channel = publisher.connect_youtube()
                st.success(f"✅ YouTube conectado: {channel}")
            except Exception as e:
                st.error(f"❌ {e}")


