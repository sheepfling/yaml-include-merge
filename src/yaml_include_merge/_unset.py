class UnsetType:
    """Sentinel type distinguishing an omitted loader tag from explicit None."""

    _instance: "UnsetType | None" = None

    def __new__(cls) -> "UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


UNSET = UnsetType()
