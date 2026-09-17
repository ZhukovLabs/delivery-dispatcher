from __future__ import annotations


class Session(dict):
    """Подписанная cookie-сессия (itsdangerous), семантика Flask.

    modified — выставляется при любой записи; middleware кладёт новую
    cookie только тогда (SESSION_REFRESH_EACH_REQUEST=False).
    """

    permanent = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.modified = False

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.modified = True

    def pop(self, k, *d):
        self.modified = True
        return super().pop(k, *d)

    def clear(self):
        self.modified = True
        super().clear()

    def setdefault(self, k, d=None):
        if k not in self:
            self.modified = True
        return super().setdefault(k, d)

    def update(self, *a, **kw):
        self.modified = True
        super().update(*a, **kw)
