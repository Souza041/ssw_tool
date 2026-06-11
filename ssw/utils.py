from datetime import datetime


def dummy() -> str:
    return str(int(datetime.now().timestamp() * 1000))


def data_ddmmaa(data: datetime) -> str:
    return data.strftime("%d%m%y")


def data_barra_curta(data: datetime) -> str:
    return f"{data.day}/{data.month}/{str(data.year)[-2:]}"