from __future__ import annotations

import json
import unittest

from src import kio_json_safe


class JsonSafeTests(unittest.TestCase):
    def test_cuts_direct_dict_cycle(self) -> None:
        value: dict[str, object] = {'status': 'ok'}
        value['self'] = value
        safe = kio_json_safe.make_json_safe(value)
        self.assertEqual(safe['self'], kio_json_safe.CIRCULAR_MARKER)
        json.dumps(safe)

    def test_cuts_nested_processed_attempts_cycle(self) -> None:
        heartbeat: dict[str, object] = {'processed': []}
        result: dict[str, object] = {'status': 'ok', 'attempts': []}
        attempt: dict[str, object] = {'attempt': 1, 'processed': heartbeat['processed']}
        result['attempts'] = [attempt]
        heartbeat['processed'].append(result)  # type: ignore[union-attr]

        safe = kio_json_safe.make_json_safe(heartbeat)
        json.dumps(safe)
        marker = safe['processed'][0]['attempts'][0]['processed']
        self.assertEqual(marker, kio_json_safe.CIRCULAR_MARKER)

    def test_shared_non_recursive_object_is_not_treated_as_cycle(self) -> None:
        shared = {'value': 1}
        safe = kio_json_safe.make_json_safe({'a': shared, 'b': shared})
        self.assertEqual(safe, {'a': {'value': 1}, 'b': {'value': 1}})
        json.dumps(safe)


if __name__ == '__main__':
    unittest.main()
