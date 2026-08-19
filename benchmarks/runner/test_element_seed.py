import os
import sys
import unittest
from unittest import mock

ELEMENT_SETUP = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tasks", "setup", "element")
sys.path.insert(0, ELEMENT_SETUP)
import seed


class SynapseRegistrationTests(unittest.TestCase):
    def test_register_uses_shared_secret_api_without_a_host_binary(self):
        with mock.patch.object(
            seed,
            "api",
            side_effect=[
                (200, {"nonce": "nonce-123"}),
                (200, {}),
            ],
        ) as api:
            seed.register("alice", admin=True)

        api.assert_has_calls(
            [
                mock.call("GET", "/_synapse/admin/v1/register"),
                mock.call(
                    "POST",
                    "/_synapse/admin/v1/register",
                    body={
                        "nonce": "nonce-123",
                        "username": "alice",
                        "password": seed.PW["alice"],
                        "admin": True,
                        "mac": "8979eed050ad31c16500d0f3be2304f524cd7e8c",
                    },
                ),
            ]
        )


if __name__ == "__main__":
    unittest.main()
