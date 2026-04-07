import logging

from config import Config
from signal_bot import SignalBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    config = Config()
    bot = SignalBot(config)
    bot.start()


if __name__ == "__main__":
    main()
