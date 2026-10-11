# ONLINE-3B — pinned offline Lichess bridge qualification

**No live Lichess, real token, irreversible BOT conversion, public deployment,
network recovery, or chess strength qualification.**

- Upstream pinned candidate: `df7e730de58cc3ef2f1415a0dc2eeda842d39167`.
- The Git revision and key tracked blob SHA1s must match the project lock.
- The separate upstream checkout is immutable. The local audited
  `extra_game_handlers.py` is overlaid only onto a temporary execution copy.
- The bridge starts `deploy/bin/allfather-online` from a relocated ONLINE-3A
  release; the sealed four-process controller retains all move authority.
- The fake API provides real loopback HTTP and NDJSON streaming. It independently
  checks all submitted UCI moves against an authoritative chess board.
- Challenge admission: standard/startpos, casual rapid 10+5, one synthetic BOT
  opponent, only one game at a time, no matchmaking or external move sources.
- Whole-game evidence must begin at startpos, include an entire legal move
  history and terminate by chess rules rather than test timeout or move cap.

## Critical upstream boundaries

The upstream bridge creates engines in separate process groups, and its main
loop may restart on network failures. Tests **must** execute in an isolated
PID namespace/container with `--network none` and `--pids-limit`. The
container exit is the final kill boundary for detached engine descendants.

Upstream may invoke abort/resign on illegal engine output. For negative tests
record and reject those HTTP requests; normal game tests require zero forbidden
attempts. Empty greetings alone do not disable chat-command responses; never
accept chat endpoint requests. Upstream's `quit_after_all_games_finish` does
not mean stop after one game: the supervisor must request bounded shutdown.

ONLINE-3B only proves offline integration. Network reconnect, uncertain move
acknowledgments, production OCI dependency locking, a final legal review, live
account upgrade and playing-strength comparison remain separate gates.
