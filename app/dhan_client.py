def build_symbol_map(self, symbols: list[str]) -> dict[str, dict]:
    """
    Build NSE symbol -> Dhan Security ID mapping.

    Dhan detailed master:
        EXCH_ID          = NSE
        SEGMENT          = E
        INSTRUMENT       = EQUITY
        UNDERLYING_SYMBOL = NSE trading symbol
        SECURITY_ID      = Dhan Security ID
    """

    df = self.security_master()

    wanted = {
        str(symbol).strip().upper()
        for symbol in symbols
        if str(symbol).strip()
    }

    if not wanted:
        return {}

    # IMPORTANT:
    # Use UNDERLYING_SYMBOL, NOT SYMBOL_NAME.
    matched = df[
        df["UNDERLYING_SYMBOL"].isin(wanted)
    ].copy()

    matched = matched.drop_duplicates(
        subset=["UNDERLYING_SYMBOL"],
        keep="first",
    )

    out: dict[str, dict] = {}

    for _, row in matched.iterrows():
        symbol = str(
            row["UNDERLYING_SYMBOL"]
        ).strip().upper()

        security_id = str(
            row["SECURITY_ID"]
        ).strip()

        if not symbol or not security_id:
            continue

        out[symbol] = {
            "security_id": security_id,
            "exchange_segment": "NSE_EQ",
            "instrument": "EQUITY",
        }

    missing = sorted(
        wanted - set(out.keys())
    )

    LOG.info(
        "Dhan Security ID mapping: %d/%d symbols matched",
        len(out),
        len(wanted),
    )

    if missing:
        for symbol in missing:
            LOG.warning(
                "Missing Dhan security_id: %s",
                symbol,
            )

    return out
