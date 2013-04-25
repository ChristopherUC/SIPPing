#!/usr/bin/python
# vi:si:et:sw=4:sts=4:ts=4
# -*- coding: UTF-8 -*-
# -*- Mode: Python -*-
#
# Copyright (C) 2013 Bertera Pietro <pietro@bertera.it>

# This file may be distributed and/or modified under the terms of
# the GNU General Public License version 2 as published by
# the Free Software Foundation.
# This file is distributed without any warranty; without even the implied
# warranty of merchantability or fitness for a particular purpose.
#
# Fleppy Backend
# Flexible Powerdns PYthon Backend

import socket
import time
import sys
import optparse
import select
import cStringIO

def_request = """OPTIONS sip:%(dest_ip)s:%(dest_port)s SIP/2.0
Via: SIP/2.0/UDP %(source_ip)s:%(source_port)s
Max-Forwards: 70
From: "fake" <sip:fake@%(source_ip)s>
To: <sip:%(dest_ip)s:%(dest_port)s>
Contact: <sip:fake@%(source_ip)s:%(source_port)s>
Call-ID: fake-id@%(source_ip)s
User-Agent: SIPPing
Date: Wed, 24 Apr 2013 20:35:23 GMT
Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, SUBSCRIBE, NOTIFY, INFO, PUBLISH
Supported: replaces, timer"""

class SipError(Exception): pass
class SipUnpackError(SipError): pass
class SipNeedData(SipUnpackError): pass
class SipPackError(SipError): pass

def canon_header(s):
	exception    = {'call-id':'Call-ID','cseq':'CSeq','www-authenticate':'WWW-Authenticate'}
    short        = ['allow-events', 'u', 'call-id', 'i', 'contact', 'm', 'content-encoding', 'e', 'content-length', 'l', 'content-type', 'c', 'event', 'o', 'from', 'f', 'subject', 's', 'supported', 'k', 'to', 't', 'via', 'v']
	s = s.lower()
    return ((len(s)==1) and s in short and canon_header(short[short.index(s)-1])) \
        or (s in exception and exception[s]) or '-'.join([x.capitalize() for x in s.split('-')])

def parse_headers(f):
	"""Return dict of HTTP headers parsed from a file object."""
	d = {}
	while 1:
		line = f.readline()
        line = line.strip()
        if not line:
            break
        l = line.split(None, 1)
        if not l[0].endswith(':'):
            raise SipUnpackError('invalid header: %r' % line)
        k = l[0][:-1].lower()
        d[k] = len(l) != 1 and l[1] or ''
	return d

def parse_body(f, headers):
    """Return SIP body parsed from a file object, given HTTP header dict."""
	if 'content-length' in headers:
        n = int(headers['content-length'])
        body = f.read(n)
        if len(body) != n:
            raise SipNeedData('short body (missing %d bytes)' % (n - len(body)))
    elif 'content-type' in headers:
        body = f.read()
    else:
        body = ''
    return body

class Message:
    """SIP Protocol headers + body."""
    __metaclass__ = type
    __hdr_defaults__ = {}
    headers = None
    body = None
    
    def __init__(self, *args, **kwargs):
        if args:
            self.unpack(args[0])
        else:
            self.headers = {}
            self.body = ''
            for k, v in self.__hdr_defaults__.iteritems():
                setattr(self, k, v)
            for k, v in kwargs.iteritems():
                setattr(self, k, v)

    
    def unpack(self, buf):
        f = cStringIO.StringIO(buf)
        # Parse headers
        self.headers = parse_headers(f)
        # Parse body
        self.body = parse_body(f, self.headers)
		# Save the rest
        self.data = f.read()

    def pack_hdr(self):
        return ''.join([ '%s: %s\r\n' % (canon_header(k),v) for k,v in self.headers.iteritems() ])
    
    def __len__(self):
        return len(str(self))
    
    def __str__(self):
        return '%s\r\n%s' % (self.pack_hdr(), self.body)

class Request(Message):
	"""SIP request."""
	__hdr_defaults__ = {
        'method':'INVITE',
        'uri':'sip:user@example.com',
        'version':'2.0',
        'headers':{ 'to':'', 'from':'', 'call-id':'', 'cseq':'', 'contact':'' }
        }
	__methods = dict.fromkeys((
        'ACK', 'BYE', 'CANCEL', 'INFO', 'INVITE', 'MESSAGE', 'NOTIFY',
        'OPTIONS', 'PRACK', 'PUBLISH', 'REFER', 'REGISTER', 'SUBSCRIBE',
        'UPDATE'
        ))
	__proto = 'SIP'

	def unpack(self, buf):
		f = cStringIO.StringIO(buf)
		line = f.readline()
		l = line.strip().split()
		if len(l) != 3 or l[0] not in self.__methods or \
			not l[2].startswith(self.__proto):
			raise SipUnpackError('invalid request: %r' % line)
		self.method = l[0]
		self.uri = l[1]
		self.version = l[2][len(self.__proto)+1:]
		Message.unpack(self, f.read())
	
	def __str__(self):
		return '%s %s %s/%s\r\n' % (self.method, self.uri, self.__proto,
                                    self.version) + Message.__str__(self)

class Response(Message):
    """SIP response."""
    __hdr_defaults__ = {
        'version':'2.0',
        'status':'200',
        'reason':'OK',
        'headers':{ 'to':'', 'from':'', 'call-id':'', 'cseq':'', 'contact':'' }
        }
    __proto = 'SIP'

	def unpack(self, buf):
        f = cStringIO.StringIO(buf)
        line = f.readline()
        l = line.strip().split(None, 2)
        if len(l) < 2 or not l[0].startswith(self.__proto) or not l[1].isdigit():
            raise SipUnpackError('invalid response: %r' % line)
        self.version = l[0][len(self.__proto)+1:]
        self.status = l[1]
        self.reason = l[2]
        Message.unpack(self, f.read())

    def __str__(self):
        return '%s/%s %s %s\r\n' % (self.__proto, self.version, self.status,
                                    self.reason) + Message.__str__(self)

def gen_request(template_vars, options):

	for i in xrange(options.count):
		
		template_vars["seq"] = i
		for k in template_vars.keys():
			if k.startswith("."):
				template_vars[k[1:]] = eval(template_vars[k])

		if options.request_template == None:
			request = def_request % template_vars

		else:
			try:
				f = open(options.request_template)
				file_request = f.read()
				f.close()
			except Exception, e:
				print "ERROR: cannot open file %s. %s" % (options.request_template, e)
				sys.exit(-1)
			try:
				request = file_request % template_vars
			except KeyError, e:
				print "ERROR: missing template variable. %s" % e	
				sys.exit(-1)
			except Exception, e:
				print "ERROR: error in template processing. %s" % e	
				sys.exit(-1)
		try:
			req = Request(request)
		except SipUnpackError, e:
			print "ERROR: malformed SIP Request. %s" % e
			sys.exit(-1)
		
		if "cseq" not in req.headers:
			req.headers["cseq"] = "%d %s" % (i, req.method)
		yield str(req)
	
def open_sock(options):
	sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
	try:
		sock.seckopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sock.seckopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
	except AttributeError:
     pass
	if options.source_port:
		sock.bind((options.source_ip, options.source_port))
	sock.settimeout(options.wait)
	return sock

def print_reply(buf, err=None, verbose=False, quiet=False):
	src_ip = buf[1][0]
	src_port = buf[1][1]
	
	try:
		resp = Response(buf[0])
	except SipUnpackError, e:
		resp = Request(buf[0])
	
	server = "%s:%s" % (src_ip,src_port)

	if not quiet:	
		if resp.__class__.__name__ == "Response":
			print "received Response %s %s from %s:%s cseq=%s" % (resp.status, resp.reason, src_ip, src_port, resp.headers['cseq'].split()[0])
			if verbose:
				print "\n=== Full Response received ===\n"
				print resp
		elif resp.__class__.__name__ == "Request":
			print "received Request %s %s from %s:%s cseq=%s" % (resp.method, resp.uri, src_ip, src_port, resp.headers['cseq'].split()[0])
			if verbose:
				print "\n=== Full Request received ===\n"
				print resp
	return True

def main():
	usage = """%prog [OPTIONS]"""
	opt = optparse.OptionParser(usage=usage)

	opt.add_option('-c', dest='count', type='int', default=sys.maxint,
                           help='Total number of queries to send')

	opt.add_option('-i', dest='wait', type='float', default=1,
                           help='Specify packet send interval time in seconds')
	
	opt.add_option('-T', dest='timeout', type='float', default=1,
                           help='Specify receiving timeout in seconds')

	opt.add_option('-v', dest='var', type='string', default=[""], action='append',
						   help='add a template variable in format varname:value')

	opt.add_option('-V', dest='verbose', default=False, action='store_true',
						   help='be verbose dumping full requests / responses')

	opt.add_option('-q', dest='quiet', default=False, action='store_true',
						   help='be quiet and never print any report')

	opt.add_option('-a', dest='aggressive', default=False, action='store_true',
						   help='aggressive mode: ignore any response')

	opt.add_option('-S', dest='source_ip', type='string', default="0.0.0.0",
                           help='Specify ip address to bind for sending and receiving UDP datagrams')

	opt.add_option('-P', dest='source_port', type='int', default=5060,
                           help='Specify the port number to use as a source port in UDP datagrams')

	opt.add_option('-d', dest='dest_ip', type='string', default=None,
                           help='*mandatory* Specify the destination ip address')

	opt.add_option('-p', dest='dest_port', type='int', default=5060,
                           help='*mandatory* Specify the destination port number')

	opt.add_option('-r', dest='request_template', type='string', default=None,
                           help='Specify the request template file')

	opt.add_option('-t', dest='print_template', action="store_true", default=False, 
							help='print the default request template')
	
	opt.add_option('-m', dest='modules', type='string', default=[], action='append',
						   help='load additionals Python modules used in Python interpreted template variables')


	options, args = opt.parse_args(sys.argv[1:])

	if options.print_template:
		print def_request
		sys.exit()
	
	for m in options.modules:
		globals()[m] = __import__(m)
	
	if not options.dest_ip:
		print "ERROR: destination ip not defined"
		opt.print_help()
		sys.exit(-1)

	template_vars = {
		"source_ip": options.source_ip,
		"source_port": options.source_port,
		"dest_ip": options.dest_ip,
		"dest_port": options.dest_port
	}

	# first var is empty by default
	for v in options.var[1:]:
		try:
			key = v.split(":")[0]
			val = "".join(v.split(":")[1:])
			template_vars.update({key: val})
		except IndexError:
			print "ERROR: variables must be in format name:value. %s" % v
			opt.print_help()
			sys.exit() 
		
	if options.verbose:
		print "=======================================" 
		print "I'm using these variables in templates: "
		print "=======================================" 
		for k in template_vars:
			print "%s: %s" % (k, template_vars[k])	
		print "=======================================" 
		print

	count = options.count
	sock = open_sock(options)
	
	sent = rcvd = ok_recvd = notify_recvd = 0 
	
	try:
		for req in gen_request(template_vars, options):
			try:
				sip_req = Request(req)
				#Add Content-Lenght if missing
				if "content-length" not in sip_req.headers:
					sip_req.headers["content-length"] = len(sip_req.body)
				
				sock.sendto(str(sip_req),(options.dest_ip, options.dest_port))
				
				if not options.quiet:	
					print "sent Request %s to %s:%d cseq=%s" % (sip_req.method, options.dest_ip, options.dest_port, sip_req.headers['cseq'].split()[0])
					if options.verbose:
						print "\n=== Full Request sent ===\n"
						print sip_req
				sent += 1
			
				if not options.aggressive:
					read = [sock]
					inputready,outputready,exceptready = select.select(read,[],[],options.timeout)
				
					for s in inputready:
						if s == sock:
							buf = None
							buf = sock.recvfrom(0xffff)
							print_reply(buf, verbose=options.verbose, quiet=options.quiet)
							rcvd += 1
	
			except socket.timeout:
				pass
			time.sleep(options.wait)
	except KeyboardInterrupt:
		pass

	if not options.quiet:
		print '\n--- statistics ---'
		print '%d packets transmitted, %d packets received, %.1f%% packet loss' % (sent, rcvd, (float(sent - rcvd) / sent) * 100)

if __name__ == '__main__':
	main()