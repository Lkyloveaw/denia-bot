import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'rag'))
import serve
from fastapi import HTTPException


class EmbedTest(unittest.TestCase):
    def test_uses_existing_embedder_and_bounds_input(self):
        old = serve._boot
        serve._boot = lambda: (None, SimpleNamespace(encode=lambda texts: np.array([[1.0, 0.0]])))
        try:
            self.assertEqual(serve.embed(serve.EmbedReq(text='hello'))['vector'], [1.0, 0.0])
            with self.assertRaises(HTTPException):
                serve.embed(serve.EmbedReq(text=' '))
            with self.assertRaises(HTTPException):
                serve.embed(serve.EmbedReq(text='x' * 2001))
        finally:
            serve._boot = old


if __name__ == '__main__':
    unittest.main()
