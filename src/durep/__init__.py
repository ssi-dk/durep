from durep.version import __version__ as __version__
from durep.version import git_repo_is_dirty


def version_info() -> str:
    if not git_repo_is_dirty:
        return __version__
    else:
        return __version__ + " (dirty repository)"
