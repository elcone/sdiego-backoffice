import logging


class SafeLogFormatter(logging.Formatter):
    def format(self, record):
        status = getattr(record, "status_code", None)
        status = status if isinstance(status, int) else "-"
        # Never serialize messages, request objects, credentials or tracebacks.
        return f"{record.levelname} {record.name} status={status}"
