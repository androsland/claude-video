"""Every name a user may pin must reach code that can actually transcribe.

`config.WHISPER_BACKENDS` is the list argparse renders as `--whisper`'s
`choices`, and the list `get_config` validates `MOVIOLA_WHISPER` against. It is
the closed set of answers to "which backend?" — and until this file, nothing
compared it to the set of backends that have an implementation behind them. The
suite already proved the *parser* and the *config reader* agree with it, which
proves the three restate one literal, not that the literal is right. A name
added to the tuple and nowhere else would pass every one of those tests, be
offered in `--whisper --help`, be accepted by argparse, be preserved by
`get_config`, and then die at `whisper.py`'s dispatch with
`Unknown whisper backend: <name>` — after the video has been downloaded, the
frames extracted, and the audio encoded.

The route each name has to complete:

  * `auto` is a SENTINEL, not a backend. `moviola.resolve_whisper_choice` maps
    it to `None` from either the flag or the config file, meaning "no pin, let
    `resolve_backend` decide". It must therefore reach NO dispatch branch, and
    a dispatch branch for it would be the bug rather than the fix.
  * `local` is `whisper.LOCAL_BACKEND`, and `transcribe_video` routes it to
    `_transcribe_local` without a key and without chunking.
  * every other name is an API backend, and needs four things to work rather
    than one: an entry in `API_CANDIDATES` so `load_api_key` can find its key,
    a branch in `_transcribe_file` so the upload happens at all, an endpoint
    and a model constant behind that branch, and an entry in `API_HOSTS` so
    `_announce_upload` names the host it is about to send audio to instead of
    falling through `.get(backend, backend)` and printing the backend's own
    name as if it were a hostname.

`TestEveryNameTranscribes` drives the whole of `transcribe_video` once per
name, with the name taken from `WHISPER_BACKENDS` rather than written here, so
adding a name to that tuple adds a test case that fails until the name has an
implementation.

**This test could not be written RED, and that is worth stating plainly.** The
four names in the tuple today all have implementations, so every assertion here
passed the first time it ran. Its only evidence is the KILL: `deepgram` added
to `WHISPER_BACKENDS` and nowhere else fails it, and so does removing `openai`
from `API_HOSTS` or from `_transcribe_file`'s dispatch. That is weaker than a
normal RED->GREEN — it demonstrates the test can see the defect, not that the
defect was ever present — and it is the strongest evidence available for a
finding whose subject is a missing check rather than a broken behaviour.

NON-GOALS, so a green run is not read as more than it is:

  * **It does not prove any backend WORKS.** `_post_whisper` and
    `_transcribe_local` are both stubbed. This pins that each name reaches an
    implementation and comes back with the segments that implementation
    returned; whether Groq's endpoint is correct, whether the model id still
    exists, and whether faster-whisper is installed are all invisible here and
    unreachable from a network-free suite.
  * **A name spelled right and routed wrong is caught, but only through the
    host.** `test_each_branch_posts_to_its_own_host` resolves the endpoint
    constant each dispatch branch really passes to `_post_whisper` and requires
    `API_HOSTS[backend]` to appear in it, so an `openai` branch copy-pasted
    from `groq`'s — which would send one provider's audio and the user's other
    key to the wrong account — fails. What it cannot see is a wrong PATH or a
    wrong MODEL on the right host: `https://api.openai.com/v1/chat/completions`
    and a model id that no longer exists both satisfy every assertion here and
    are only discoverable against the live API.
  * **The legitimate configuration it must not fire on is the tuple as it
    stands.** Four names, one of them a sentinel with deliberately no
    implementation. A test that demanded a dispatch branch per name would fail
    on `auto` — correctly configured — which is why `auto` is asserted to have
    NO branch rather than exempted from the loop.
  * **It says nothing about whether the set is the RIGHT set.** A provider
    moviola ought to support and does not is invisible from here, exactly as
    the sibling `test_bounded_failures.py` records for the parser-vs-config
    comparison it makes.
  * **The dispatch names are read out of `_transcribe_file`'s AST**, so this
    breaks if that function is ever restructured away from `backend == "..."`
    comparisons — a dispatch dict, or a registry, would read as zero branches
    and fail. That is a maintenance cost accepted on purpose: reading the
    literal list a second time would make the comparison a tautology, which is
    the exact failure this file exists to correct. The call shape is read the
    same structural way and has the same limit, which was undisclosed until a
    review named it: `_dispatch_endpoints` matches an `ast.Name` whose id is
    `_post_whisper`, so a call written as `whisper._post_whisper(...)`, through
    an alias, or behind a wrapper is not seen. That failure is loud rather than
    silent — a branch with no matching call reads as no route and trips
    `no _post_whisper call found in the ... branch` — but the message names the
    wrong cause, and this paragraph is where the next reader finds the right
    one.

Every value written below is inert filler. Nothing here reads a real credential.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import config
import moviola
import whisper


FILLER = "placeholder-value-not-a-credential"

SENTINEL = "auto"
API_BACKENDS = tuple(n for n in config.WHISPER_BACKENDS if n not in (SENTINEL, whisper.LOCAL_BACKEND))
IMPLEMENTED = tuple(n for n in config.WHISPER_BACKENDS if n != SENTINEL)


def _dispatch_names() -> set[str]:
    """The string literals `_transcribe_file` compares `backend` against.

    Read out of the AST rather than copied, so this set is derived from the
    implementation and not from the same literal the implementation was
    supposed to be checked against.
    """
    tree = ast.parse(inspect.getsource(whisper._transcribe_file))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "backend"):
            continue
        for op, other in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(other, ast.Constant):
                if isinstance(other.value, str):
                    found.add(other.value)
    return found


def _dispatch_endpoints() -> dict[str, tuple[str, str]]:
    """backend name -> the (endpoint, model) constants ITS branch actually posts to.

    Read the same way and for the same reason as `_dispatch_names`. This is the
    half that catches a branch which is named right and wired wrong: the
    constant is resolved off the module, so the value compared below is the URL
    the upload would really use.
    """
    tree = ast.parse(inspect.getsource(whisper._transcribe_file))
    routes: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if not (isinstance(test.left, ast.Name) and test.left.id == "backend"):
            continue
        if not (len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)):
            continue
        name = test.comparators[0].value
        for call in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if not isinstance(call, ast.Call):
                continue
            if not (isinstance(call.func, ast.Name) and call.func.id == "_post_whisper"):
                continue
            if len(call.args) >= 3 and all(isinstance(a, ast.Name) for a in (call.args[0], call.args[2])):
                routes[name] = (
                    getattr(whisper, call.args[0].id),
                    getattr(whisper, call.args[2].id),
                )
            break
    return routes


class TestTheTableAndTheDispatchAgree:
    """The set of offered names and the set of implemented names are one set."""

    def test_the_ast_reader_found_something(self):
        # Guards every assertion below: an empty set would make them all pass
        # vacuously if `_transcribe_file` were ever restructured.
        assert _dispatch_names(), "no `backend == \"...\"` comparisons found"

    def test_every_api_name_has_a_dispatch_branch(self):
        assert set(API_BACKENDS) <= _dispatch_names()

    def test_no_dispatch_branch_is_unreachable(self):
        # The other direction: a branch for a name argparse rejects can never
        # run, so it is dead code that reads as support.
        assert _dispatch_names() <= set(config.WHISPER_BACKENDS)

    def test_every_api_name_can_have_its_key_found(self):
        assert set(API_BACKENDS) == {backend for _, backend in whisper.API_CANDIDATES}

    def test_every_api_name_names_a_host(self):
        # `_announce_upload` does `API_HOSTS.get(backend, backend)`, so a
        # missing entry degrades silently into printing "uploading to openai"
        # rather than the hostname the audio is actually going to.
        assert set(API_BACKENDS) == set(whisper.API_HOSTS)

    def test_the_sentinel_has_no_implementation_and_should_not(self):
        assert SENTINEL not in _dispatch_names()
        assert SENTINEL not in whisper.API_HOSTS
        assert SENTINEL != whisper.LOCAL_BACKEND

    def test_the_sentinel_never_reaches_the_dispatch(self):
        # Both directions of `resolve_whisper_choice`: the flag and the config
        # file. "auto" means no pin, so `None` is what travels onward.
        assert moviola.resolve_whisper_choice(SENTINEL, "groq") is None
        assert moviola.resolve_whisper_choice(None, SENTINEL) is None

    def test_the_local_name_is_the_local_constant(self):
        assert whisper.LOCAL_BACKEND in config.WHISPER_BACKENDS


class TestTheApiRouteIsComplete:
    """Each API backend has its own endpoint and its own model, not a shared pair."""

    @pytest.mark.parametrize("endpoint,model", [
        (whisper.GROQ_ENDPOINT, whisper.GROQ_MODEL),
        (whisper.OPENAI_ENDPOINT, whisper.OPENAI_MODEL),
    ], ids=["groq", "openai"])
    def test_the_constants_are_populated(self, endpoint, model):
        assert endpoint.startswith("https://")
        assert model

    @pytest.mark.parametrize("backend", API_BACKENDS)
    def test_each_branch_posts_to_its_own_host(self, backend):
        # Ties three independently-maintained tables together: the dispatch
        # branch, the endpoint constant it names, and API_HOSTS. A branch that
        # was copy-pasted and had only its name changed fails here.
        routes = _dispatch_endpoints()
        assert backend in routes, f"no _post_whisper call found in the {backend} branch"
        endpoint, model = routes[backend]
        assert whisper.API_HOSTS[backend] in endpoint
        assert model

    def test_no_two_branches_share_a_route(self):
        routes = _dispatch_endpoints()
        assert len(set(routes.values())) == len(routes)

    def test_the_two_providers_do_not_share_a_pair(self):
        # A copy-pasted branch that reused one provider's endpoint would post
        # one provider's audio to the other's account with the other's key.
        assert whisper.GROQ_ENDPOINT != whisper.OPENAI_ENDPOINT
        assert whisper.GROQ_MODEL != whisper.OPENAI_MODEL

    @pytest.mark.parametrize("backend", API_BACKENDS)
    def test_the_host_is_a_hostname_and_not_the_backend_name(self, backend):
        host = whisper.API_HOSTS[backend]
        assert host != backend
        assert "." in host


class TestEveryNameTranscribes:
    """Drive the real dispatch once per implemented name."""

    @pytest.fixture
    def stubbed(self, monkeypatch, tmp_path):
        """Replace the two things that would leave this machine, nothing else."""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"\x00" * 2048)

        monkeypatch.setattr(whisper, "extract_audio", lambda *a, **k: audio)
        monkeypatch.setattr(
            whisper,
            "_post_whisper",
            # Parameter names track the real signatures — `_post_whisper`
            # and `_transcribe_local` both call theirs `audio_path`. A stub
            # that renames it silently stops standing in for a keyword call.
            lambda endpoint, api_key, model, audio_path: {
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]
            },
        )
        monkeypatch.setattr(
            whisper,
            "_transcribe_local",
            lambda audio_path, options: [{"start": 0.0, "end": 1.0, "text": "hello"}],
        )
        return tmp_path

    @pytest.mark.parametrize("backend", IMPLEMENTED)
    def test_a_pinned_name_reaches_an_implementation(self, backend, stubbed):
        # api_key is passed for every name; the local branch ignores it, which
        # is itself part of the contract being pinned.
        segments, used, _gaps = whisper.transcribe_video(
            str(stubbed / "video.mp4"),
            stubbed / "out.mp3",
            backend=backend,
            api_key=FILLER,
        )
        assert used == backend
        assert segments == [{"start": 0.0, "end": 1.0, "text": "hello"}]

    def test_an_unknown_name_is_refused_rather_than_ignored(self):
        # The failure mode the table is supposed to prevent, shown directly:
        # a name with no branch raises this, and it raises it after the audio
        # has already been encoded.
        with pytest.raises(SystemExit, match="Unknown whisper backend"):
            whisper._transcribe_file("deepgram", FILLER, Path("audio.mp3"))

    @pytest.mark.parametrize("backend", config.WHISPER_BACKENDS)
    def test_the_parser_accepts_every_offered_name(self, backend):
        args = moviola.build_parser().parse_args(["src", "--whisper", backend])
        assert args.whisper == backend
