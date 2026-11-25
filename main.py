import logging
import signal

import shutdown
from config import Config
from signal_bot import SignalBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    # `docker stop` (SIGTERM) becomes SystemExit on the main thread, so the
    # in-flight message's finally blocks run (temp files securely deleted,
    # received attachments deleted from signal-cli). Delete sweeps are never
    # interrupted: the exit is postponed until they finish (see shutdown.py),
    # and files of messages still queued are deleted before exiting.
    signal.signal(signal.SIGTERM, shutdown.request)
    config = Config()
    bot = SignalBot(config)
    bot.start()


if __name__ == "__main__":
    main()
