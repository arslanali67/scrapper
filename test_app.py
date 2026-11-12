"""Offline checks for app.py's form -> command mapping (no network, no spend). Run: python test_app.py"""
import app


def test_form_becomes_safe_command():
    argv, out = app.build_command({"scraper": "google", "query": "dentists", "location": "Austin, TX", "limit": "10",
                                   "max_cost": "0.5", "verify": True, "dry": "true"})
    assert argv[4].endswith("google_scraper.py") and "--query=dentists" in argv and "--location=Austin, TX" in argv
    assert "--verify" in argv and "--dry-run" in argv and "--limit=10" in argv and out.parent == app.OUT
    argv, _ = app.build_command({"scraper": "people", "titles": "--out=evil.csv", "location": "Texas"})
    assert "--titles=--out=evil.csv" in argv and sum(a.startswith("--out=") for a in argv) == 1  # a value is never a flag
    argv, _ = app.build_command({"scraper": "company", "urls": "x https://www.linkedin.com/company/acme/\nnot a url"})
    url_file = next(a for a in argv if a.startswith("--input=")).split("=", 1)[1]
    assert open(url_file, encoding="utf-8").read() == "https://www.linkedin.com/company/acme/\n"
    app.Path(url_file).unlink()
    for bad in ({"scraper": "google", "query": "dentists"}, {"scraper": "people"}, {"scraper": "company"},
                {"scraper": "rm"}, {"scraper": "google", "query": "q", "location": "l", "limit": "0"}):
        try:
            app.build_command(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_only_csvs_in_outputs_can_be_read():
    assert app.safe_csv("../.env") is None and app.safe_csv("..\\common.py") is None and app.safe_csv("") is None


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for f in tests:
        f()
        print("ok", f.__name__)
    print(f"{len(tests)} checks passed")
