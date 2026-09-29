# The `python -m mainplate` entry point, which is what the systemd unit invokes.
#
# It exists so the unit can name `{sys.executable} -m mainplate` rather than a console script: the
# interpreter path is always absolute and always the one holding this package, while the shim's
# location depends on how the package was installed.

from mainplate.main import main

main()
