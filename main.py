import logging
import signal

from config import Config
from signal_bot import SignalBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _stop(signum, frame) -> None:
    # Turn `docker stop` (SIGTERM) into SystemExit on the main thread, so the
    # in-flight message's finally blocks run: temp files are securely deleted
    # and received attachments are deleted from signal-cli.
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    config = Config()
    bot = SignalBot(config)
    bot.start()


if __name__ == "__main__":
    main()
