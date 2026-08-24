from .minervini import evaluate as minervini
from .qullamaggie import evaluate as qullamaggie
from .oneil import evaluate as oneil
from .weinstein import evaluate as weinstein
from .darvas import evaluate as darvas
from .connors import evaluate as connors

STRATEGIES = {
    "Minervini": minervini,
    "Qullamaggie": qullamaggie,
    "CAN SLIM": oneil,
    "Weinstein": weinstein,
    "Darvas": darvas,
    "Connors": connors,
}

