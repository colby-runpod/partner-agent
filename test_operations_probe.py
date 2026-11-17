import io,unittest
import operations_probe as p
class Response(io.BytesIO):
 status=200
class ProbeTests(unittest.TestCase):
 def test_requires_worker_readiness_not_listener_success(self):
  for payload in (b'{"status":"listening"}',b'{"ready":false}',b'not json',b'{"ready":"true"}'):
   self.assertFalse(p.probe('url',lambda *a,**k:Response(payload)))
  self.assertTrue(p.probe('url',lambda *a,**k:Response(b'{"ready":true}')))
 def test_timeout_is_not_healthy(self):
  def fail(*a,**k):raise TimeoutError()
  self.assertFalse(p.probe('url',fail))
