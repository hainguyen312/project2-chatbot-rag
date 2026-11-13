import pytest

from services import auth_guard
from services.roles import RoleStore


class FakeCol:
    def __init__(self):
        self.docs = {}

    def find_one(self, q):
        return self.docs.get(q["_id"])

    def update_one(self, q, upd, upsert=False):
        self.docs.setdefault(q["_id"], {"_id": q["_id"]}).update(upd["$set"])

    def delete_one(self, q):
        self.docs.pop(q["_id"], None)

    def find(self, q):
        return list(self.docs.values())


def _verify(claims):
    def v(token):
        if token != "good":
            raise ValueError("bad token")
        return claims
    return v


ADMIN = {"uid": "u1", "email": "Admin@Example.com", "email_verified": True}


@pytest.fixture
def store():
    s = RoleStore(FakeCol())
    s.set_role("  ADMIN@example.com ", "admin")
    return s


def test_store_normalizes_email_and_revokes(store):
    assert store.get_role("admin@example.com") == "admin"
    store.set_role("admin@example.com", None)
    assert store.get_role("admin@example.com") is None


def test_store_rejects_bad_input(store):
    with pytest.raises(ValueError):
        store.set_role("not-an-email", "admin")
    with pytest.raises(ValueError):
        store.set_role("x@y.com", "superuser")


def test_admin_allowed(store):
    out = auth_guard.require_role("admin", "Bearer good", _verify(ADMIN), store.get_role)
    assert out == {"uid": "u1", "email": "admin@example.com", "role": "admin"}


@pytest.mark.parametrize("auth,claims,status", [
    (None, ADMIN, 401),
    ("Bearer bad", ADMIN, 401),
    ("Bearer good", {**ADMIN, "email_verified": False}, 403),
    ("Bearer good", {**ADMIN, "email": "other@example.com"}, 403),
    ("Bearer good", {"uid": "u1"}, 403),
])
def test_admin_denied(store, auth, claims, status):
    with pytest.raises(auth_guard.AuthError) as e:
        auth_guard.require_role("admin", auth, _verify(claims), store.get_role)
    assert e.value.status == status


def test_role_store_down_is_503():
    def boom(_):
        raise RuntimeError("mongo down")
    with pytest.raises(auth_guard.AuthError) as e:
        auth_guard.require_role("admin", "Bearer good", _verify(ADMIN), boom)
    assert e.value.status == 503
