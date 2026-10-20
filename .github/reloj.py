"""Reloj de publicación: lanza fabrica.yml en cada franja con workflow_dispatch.

Los horarios `schedule` de GitHub no se disparaban en este repositorio, así que este proceso
espera a cada franja y la lanza él mismo. Corre unas 5 horas; reloj.yml ya dejó en cola al
siguiente reloj, que toma el relevo en cuanto este termina.
"""
import datetime
import subprocess
import time

# Horas en UTC (Colombia es UTC-5): (hora, minuto, tipo, nicho, redes).
REELS = [(12, "misterio"), (15, "curiosidades"), (18, "curiosidades"), (21, "misterio"), (0, "misterio"), (3, "curiosidades")]
YOUTUBE_HOURS = {18, 0}
POST_HOURS = [0, 1, 2, 12, 13, 14, 16, 17, 18, 20, 21, 22]
MYSTERY_POST_HOURS = {12, 14, 17, 20, 22, 1}

SLOTS = [(h, 7, "reel", niche, "instagram,facebook" + (",youtube" if h in YOUTUBE_HOURS else "")) for h, niche in REELS]
SLOTS += [(h, 37, "post", "misterio" if h in MYSTERY_POST_HOURS else "curiosidades", "instagram,facebook") for h in POST_HOURS]

RUN_SECONDS = 5 * 3600        # el trabajo tiene un límite de 6 h
HANDOFF_MARGIN = 10 * 60      # no ceder el turno si una franja está a menos de esto


def next_slot(now):
    candidates = []
    for hour, minute, *rest in SLOTS:
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += datetime.timedelta(days=1)
        candidates.append((at, rest))
    return min(candidates, key=lambda c: c[0])


def main():
    deadline = time.time() + RUN_SECONDS
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        at, (kind, niche, publish_to) = next_slot(now)
        wait = (at - now).total_seconds()
        if time.time() + wait > deadline and wait > HANDOFF_MARGIN:
            print(f"Relevo: la próxima franja ({at:%H:%M} UTC) la lanza el siguiente reloj.")
            return
        print(f"Esperando a las {at:%H:%M} UTC: {kind} de {niche} -> {publish_to}", flush=True)
        time.sleep(max(0, wait))
        subprocess.run(["gh", "workflow", "run", "fabrica.yml", "-f", f"kind={kind}", "-f", f"niche={niche}",
                        "-f", f"publish_to={publish_to}"], check=False)
        time.sleep(61)   # no volver a calcular la misma franja


if __name__ == "__main__":
    main()
