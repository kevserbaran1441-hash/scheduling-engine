"""Motorun kendi hata türleri. CLI bunları yakalayıp okunur mesaj basar."""


class EngineError(Exception):
    """Tüm motor hatalarının tabanı."""


class InputError(EngineError):
    """Geçersiz girdi dosyası, seçenek veya sıralama."""


class UnsupportedInputError(EngineError):
    """İstenen yöntem/ölçüt eldeki veriyle desteklenmiyor (ör. due_date yok)."""


class InfeasibleScheduleError(EngineError):
    """Ortak feasibility checker çizelgeyi reddetti."""
