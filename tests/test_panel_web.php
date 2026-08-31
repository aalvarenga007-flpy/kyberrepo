<?php
require __DIR__ . '/../sync/web_auth.php';
function check($ok, $label) { if (!$ok) { fwrite(STDERR, "FAIL: $label\n"); exit(1); } echo "OK: $label\n"; }
$root = sys_get_temp_dir() . '/kyber-web-test-' . bin2hex(random_bytes(8));
mkdir($root, 0700); mkdir($root.'/leases', 0700); mkdir($root.'/tickets', 0700);
$lease = bin2hex(random_bytes(32));
file_put_contents($root.'/leases/'.$lease, json_encode(array('uid'=>1, 'expires'=>time()+60)));
$record = array('uid'=>1, 'company'=>'ekaru', 'lease'=>$lease, 'expires'=>time()+30, 'session_expires'=>time()+60);
function ticket($root, $record) { $t=bin2hex(random_bytes(32)); file_put_contents($root.'/tickets/'.hash('sha256',$t),json_encode($record)); return $t; }
$t=ticket($root,$record);
check(panel_consume_ticket($t,'ekaru',$root) !== null, 'valid ticket');
check(panel_consume_ticket($t,'ekaru',$root) === null, 'replay denied');
check(panel_consume_ticket(ticket($root,$record),'ejapo',$root) === null, 'cross-company denied');
check(panel_consume_ticket('../lease','ekaru',$root) === null, 'path traversal denied');
check(panel_consume_ticket(ticket($root,array_merge($record,array('expires'=>time()-1))),'ekaru',$root) === null, 'expired denied');
unlink($root.'/leases/'.$lease);
check(!panel_lease_valid($record,$root), 'logout revocation');
putenv('KYBER_PANEL_ORIGIN=https://kyber.example:8443');
$csrf=bin2hex(random_bytes(32));
check(panel_csrf_valid('POST',$csrf,$csrf,'https://kyber.example:8443'),'CSRF valid');
check(!panel_csrf_valid('GET',$csrf,$csrf,'https://kyber.example:8443'),'GET denied');
check(!panel_csrf_valid('POST','wrong',$csrf,'https://kyber.example:8443'),'CSRF mismatch denied');
check(!panel_csrf_valid('POST',$csrf,$csrf,'https://attacker.example'),'origin mismatch denied');
$safe=panel_redact(array('endpoint_url'=>'hidden','password'=>'hidden','text'=>'url?api_key=secret&x=1'));
check(!isset($safe['password']) && !isset($safe['endpoint_url']) && strpos($safe['text'],'secret') === false,'redaction');
foreach(glob($root.'/tickets/*') as $f) unlink($f);
rmdir($root.'/tickets'); rmdir($root.'/leases'); rmdir($root);
