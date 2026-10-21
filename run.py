"""
AI Reel Factory - Entry Point

Uso:
  python run.py                                   -> Interfaz web (Streamlit)
  python run.py --cli --topic "Tema" --niche ciencia --duration 30
  python run.py --cli --topic aleatorio --niche finanzas --platform tiktok --languages es,en
  python run.py --cli --topic "La caída de Constantinopla" --niche historia_oculta --research
  python run.py --cli --topic "Tema" --job-id reel_..._Tema   -> Reanuda un trabajo interrumpido
  python run.py --batch temas.txt --workers 2     -> Lote desde archivo (TXT o CSV)
  python run.py --auto-topics 5 --niche finanzas  -> Lote con temas propuestos por la IA
  python run.py --check                           -> Diagnóstico de FFmpeg, claves y proveedores
"""

import os
import sys
import argparse
import datetime
import subprocess

# Consola UTF-8 en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fábrica de Reels Automatizados con IA")
    parser.add_argument("--cli", action="store_true", help="Generar un reel por consola")
    parser.add_argument("--post", action="store_true", help="Crear un post de imagen (titular sobre la imagen) por consola")
    parser.add_argument("--news", action="store_true", help="Crear un post con la última noticia de Colombia para la página de noticias")
    parser.add_argument("--topic", type=str, default="El Secreto Mejor Guardado del Océano", help="Tema del Reel")
    parser.add_argument("--niche", type=str, default="curiosidades", help="Nicho del contenido")
    parser.add_argument("--duration", type=int, default=30, help="Duración objetivo en segundos (ej: 15, 30, 60)")
    parser.add_argument("--scenes", type=int, default=None, help="Cantidad exacta de escenas a generar")
    parser.add_argument("--aspect-ratio", type=str, default="9:16", choices=["9:16", "1:1", "16:9"], help="Formato de video")
    parser.add_argument("--voice", type=str, default=None, help="ID de voz Edge-TTS (por defecto la de config.yaml)")
    parser.add_argument("--rate", type=str, default=None, help="Velocidad de locución (ej: +0%%, +10%%, -5%%)")
    parser.add_argument("--no-subtitles", action="store_true", help="Desactivar subtítulos")
    parser.add_argument("--zoom", type=float, default=1.0, help="Intensidad de movimiento de cámara (0 = estático, 1 = normal, 1.5 = dramático)")
    parser.add_argument("--style", type=str, default="yellow_pop",
                        choices=["yellow_pop", "green_neon", "cyan_electric", "red_fire", "white_pure", "director", "none"],
                        help="Estilo de subtítulos ('director' usa el color que eligió la IA en cada escena)")
    parser.add_argument("--profile", type=str, default=None, choices=["very_fast", "balanced_fast", "cinematic"], help="Perfil de producción")
    parser.add_argument("--job-id", type=str, default=None, help="Reanudar/forzar un job_id concreto")
    parser.add_argument("--batch", type=str, default=None, help="Archivo de temas (.txt 'tema | nicho | duración' o .csv)")
    parser.add_argument("--auto-topics", type=int, default=0, help="Generar N temas con IA para el nicho y producirlos en lote")
    parser.add_argument("--batch-name", type=str, default=None, help="Nombre del lote (mismo nombre = reanudar lote)")
    parser.add_argument("--workers", type=int, default=1, help="Videos en paralelo en modo lote (recomendado 1-3)")
    parser.add_argument("--platform", type=str, default=None, choices=["tiktok", "reels"],
                        help="tiktok: 65-90 s (apto para el Programa de Creadores) | reels: 30-60 s")
    parser.add_argument("--research", dest="research", action="store_true", default=None,
                        help="Investigar el tema (Wikipedia + búsqueda) y escribir un documental basado en hechos")
    parser.add_argument("--no-research", dest="research", action="store_false", help="Desactivar la investigación")
    parser.add_argument("--languages", type=str, default="es", help="Idiomas separados por coma, p. ej. es,en")
    parser.add_argument("--publish-to", type=str, default=None,
                        help="Redes donde publicar este video, separadas por coma (instagram,facebook,youtube); "
                             "por defecto las de config.yaml")
    parser.add_argument("--check", action="store_true", help="Diagnóstico del entorno")
    parser.add_argument("--port", type=int, default=8501, help="Puerto para Streamlit")
    return parser


def run_check() -> int:
    from src.pipeline import ReelFactoryPipeline
    pipeline = ReelFactoryPipeline()
    info = pipeline.preflight()
    print(f"FFmpeg/FFprobe: {'OK' if info['ffmpeg'] else 'NO ENCONTRADO'}")
    router = pipeline.script_gen.llm
    providers = router.available_providers()
    if not providers:
        print("Guionista IA: sin claves (se usará el modo offline genérico). Añade GEMINI_API_KEY gratis en https://aistudio.google.com")
    for provider in providers:
        ok, message = router.ping(provider)
        print(f"Guionista IA '{provider}': {'OK' if ok else 'FALLA - ' + message}")
    print(f"Pexels: {'configurado' if info['pexels'] else 'sin clave'}")
    print(f"Nichos: {info['niche_count']} | Perfil: {info['policy']['profile']}")
    return 0 if info["ready"] else 1


def run_batch_cli(args) -> int:
    from src.batch import BatchItem, parse_batch_file, run_batch
    if args.batch:
        items = parse_batch_file(args.batch, args.niche, args.duration)
        name = args.batch_name or os.path.splitext(os.path.basename(args.batch))[0]
    else:
        # Cada video del lote resuelve su propio tema aleatorio (sin repetir) al producirse.
        items = [BatchItem(topic="aleatorio", niche=args.niche, duration=args.duration) for _ in range(args.auto_topics)]
        name = args.batch_name or f"auto_{args.niche}_{datetime.datetime.now():%Y%m%d_%H%M}"
    if not items:
        print("No hay temas para producir.")
        return 1

    print(f"🏭 Lote '{name}': {len(items)} videos con {args.workers} trabajador(es).")

    def progress(outcome, done, total):
        icon = {"completed": "✅", "degraded": "⚠️", "skipped": "⏭️", "failed": "❌"}.get(outcome.status, "•")
        extra = f" ({outcome.error[:120]})" if outcome.error else ""
        extra += f" [{outcome.warnings} avisos]" if outcome.warnings else ""
        print(f"{icon} [{done}/{total}] {outcome.topic} -> {outcome.status} en {outcome.seconds}s{extra}")

    options = {"voice": args.voice, "subtitle_style": args.style, "production_profile": args.profile,
               "platform": args.platform, "research": args.research}
    outcomes = run_batch(items, name, workers=args.workers, options=options, on_progress=progress)
    failed = [o for o in outcomes if o.status == "failed"]
    print(f"\nResumen: {len(outcomes) - len(failed)} listos, {len(failed)} fallidos. Informe: output/lotes/{name}.csv")
    if failed:
        print(f"Relanza el mismo comando con --batch-name {name} para reintentar solo los fallidos.")
    return 1 if failed else 0


def publish_cli(pipeline, package_path: str, publish_to) -> int:
    from src.core.publisher import auto_publish
    settings = dict(pipeline.config.get("publishing", {}))
    if publish_to:
        settings.update(auto=True, platforms=[p.strip() for p in publish_to.split(",") if p.strip()])
    published, skipped = auto_publish(package_path, settings)
    if skipped:
        print(f"   ⚠️ {skipped}")
    for platform, info in published.items():
        print(f"   📤 {platform}: {info.get('url') if info.get('ok') else 'FALLÓ - ' + info.get('error', '')}")
    # Código de salida 1 si se pidió publicar y no se pudo: en la nube eso avisa por correo.
    failed = skipped or any(not info.get("ok") for info in published.values())
    return 1 if publish_to and (failed or not published) else 0


def main() -> int:
    from src.core.env import load_env
    load_env()
    args, _ = build_parser().parse_known_args()

    if args.check:
        return run_check()
    if args.batch or args.auto_topics:
        return run_batch_cli(args)
    if args.cli:
        from src.pipeline import ReelFactoryPipeline
        target = f"perfil {args.platform}" if args.platform else f"{args.duration}s"
        print(f"🎬 Tema: {args.topic} | Nicho: {args.niche} | Duración: {target} | Idiomas: {args.languages}")
        pipeline = ReelFactoryPipeline()
        results = pipeline.generate_multilang(
            languages=[lang.strip() for lang in args.languages.split(",") if lang.strip()],
            platform=args.platform,
            research=args.research,
            topic=args.topic,
            niche=args.niche,
            voice=args.voice,
            voice_rate=args.rate,
            target_duration=args.duration,
            scene_count=args.scenes,
            aspect_ratio=args.aspect_ratio,
            enable_subtitles=not args.no_subtitles,
            subtitle_style=args.style,
            zoom_intensity=args.zoom,
            production_profile=args.profile,
            job_id=args.job_id,
        )
        for res in results:
            print(f"\n🎉 [{res.language}] {res.title}\n   Video: {res.video_path}")
            print(f"   ⏱️ {res.duration:.1f}s | Estado: {res.status} | Guion: {res.script.source}")
            for warning in res.warnings:
                print(f"   ⚠️ [{warning['step']}] {warning['message']}")
            print(f"   📦 Publicación: {res.publication_package}")
        return publish_cli(pipeline, results[0].publication_package, args.publish_to)
    if args.news:
        from src.pipeline import ReelFactoryPipeline
        # La página de noticias es otra página de Facebook: sus credenciales sustituyen a las de la
        # principal, y sin ellas no se publica en ninguna (nunca una noticia en la página de misterio).
        news_page = {"META_PAGE_ID": os.getenv("NEWS_PAGE_ID", ""), "META_PAGE_TOKEN": os.getenv("NEWS_PAGE_TOKEN", "")}
        for key in ("META_PAGE_ID", "META_PAGE_TOKEN", "INSTAGRAM_USER_ID"):
            os.environ.pop(key, None)
        if all(news_page.values()):
            os.environ.update(news_page)
        else:
            print("   ⚠️ Página de noticias sin conectar (NEWS_PAGE_ID y NEWS_PAGE_TOKEN): el post no se publicará.")
        pipeline = ReelFactoryPipeline()
        package_path = pipeline.create_news_post()
        print(f"   📦 Publicación: {package_path}")
        return publish_cli(pipeline, package_path, args.publish_to)
    if args.post:
        from src.pipeline import ReelFactoryPipeline
        print(f"🖼️ Post | Tema: {args.topic} | Nicho: {args.niche}")
        pipeline = ReelFactoryPipeline()
        package_path = pipeline.create_post(args.topic, args.niche, research=args.research)
        print(f"   📦 Publicación: {package_path}")
        return publish_cli(pipeline, package_path, args.publish_to)

    app_path = os.path.abspath("src/ui/app.py")
    print(f"🚀 Iniciando servidor web Streamlit en http://localhost:{args.port}...")
    return subprocess.run([
        sys.executable, "-m", "streamlit", "run", app_path,
        "--server.port", str(args.port), "--server.headless", "false", "--theme.base", "dark",
    ]).returncode


if __name__ == "__main__":
    sys.exit(main())
