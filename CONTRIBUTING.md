# Contributing

Start with **[docs/contributing.md](docs/contributing.md)** (the rules: no guessed
parm names, no unproven success claims, licence audit) and
**[docs/adding-a-skill.md](docs/adding-a-skill.md)** (file-by-file walkthrough).

Before opening a PR:

```bash
python tools/make_registry.py                                        # regenerate the catalog
python -m unittest discover -s tests -p "test_tier_a*.py"            # needs no Houdini
hython -m unittest discover -s tests -p "test_tier_b*.py"            # needs a licensed Houdini
```

One line that decides most reviews: **what did you run, on which build, and what
did it print?** If the answer is "nothing yet", say that in the PR description -
an honest unreproduced claim gets merged as a note; a silent one gets closed.
