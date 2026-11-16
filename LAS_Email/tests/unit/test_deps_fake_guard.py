"""assert_no_fakes_in_prod: a fake adapter wired into a staging/prod Deps must fail startup."""

import pytest

from las.common.db import SessionFactory
from las.common.deps import assert_no_fakes_in_prod, build_fake_deps
from las.common.settings import Settings


def _settings(env: str) -> Settings:
    return Settings(_env_file=None, env=env)


def test_dev_env_is_never_checked(db: SessionFactory) -> None:
    deps = build_fake_deps(_settings("dev"), db)
    assert_no_fakes_in_prod(deps)  # all fakes, dev env -- must not raise


def test_test_env_is_never_checked(db: SessionFactory) -> None:
    deps = build_fake_deps(_settings("test"), db)
    assert_no_fakes_in_prod(deps)  # all fakes, test env -- must not raise


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_fakes_in_prod_raise(db: SessionFactory, env: str) -> None:
    # build_fake_deps wires every external system to a las.common.fakes test double -- this is
    # exactly the mistake the guard exists to catch, regardless of which Deps field it's wired to.
    deps = build_fake_deps(_settings(env), db)
    with pytest.raises(RuntimeError, match="fake adapters wired"):
        assert_no_fakes_in_prod(deps)
