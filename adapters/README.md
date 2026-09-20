# Adapters

Per-engine adapters will normalize lifecycle control and observations while preserving native engine semantics.

Target capability surface:

```text
set_position(...)
search(region, budget)
snapshot()
stop()
verify(candidate, budget)
```

Generation 1 adapters will use process-isolated UCI communication.
