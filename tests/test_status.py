import unittest
from hq.status import session_status

class StatusTests(unittest.TestCase):
    def test_status_rules(self):
        for last, kind, ended, expected in [(990,'assistant',None,'working'),(990,'turn_end',None,'waiting'),(300,'assistant',None,'idle'),(1000-86400-1,'assistant',None,'done'),(999,'assistant',995,'done'),(999,'error',None,'error'),(700,'tool_call',None,'working'),(300,'tool_call',None,'idle')]:
            with self.subTest(kind=kind, last=last): self.assertEqual(session_status(last,kind,ended,1000), expected)
