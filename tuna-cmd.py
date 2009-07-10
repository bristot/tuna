#! /usr/bin/python
# -*- python -*-
# -*- coding: utf-8 -*-
#   tuna - Application Tuning GUI
#   Copyright (C) 2008, 2009 Red Hat Inc.
#   Arnaldo Carvalho de Melo <acme@redhat.com>
#
#   This application is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; version 2.
#
#   This application is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   General Public License for more details.

import getopt, ethtool, fnmatch, inet_diag, os, procfs, re, schedutils, sys
from tuna import tuna, sysfs

import gettext
import locale

try:
	from sets import Set as set
except:
	# OK, we're modern, having sets as first class citizens
	pass

# FIXME: ETOOMANYGLOBALS, we need a class!

nr_cpus = None
ps = None
irqs = None
version = "0.9"

def usage():
	print _('Usage: tuna [OPTIONS]')
	fmt = '\t%-40s %s'
	print fmt % ('-h, --help',		    _('Give this help list'))
	print fmt % ('-g, --gui',		    _('Start the GUI'))
	print fmt % ('-c, --cpus=' + _('CPU-LIST'), _('CPU-LIST') + ' ' + _('affected by commands'))
	print fmt % ('-C, --affect_children',	    _('Operation will affect children threads'))
	print fmt % ('-f, --filter',		    _('Display filter the selected entities'))
	print fmt % ('-i, --isolate',		    _('Move all threads away from') + ' ' + _('CPU-LIST'))
	print fmt % ('-I, --include',		    _('Allow all threads to run on') + ' ' + _('CPU-LIST'))
	print fmt % ('-K, --no_kthreads',	    _('Operations will not affect kernel threads'))
	print fmt % ('-m, --move',		    _('Move selected entities to') + ' ' + _('CPU-LIST'))
	print fmt % ('-n, --show_sockets',	    _('show network sockets in use by threads'))
	print fmt % ('-p, --priority=[' +
		     _('POLICY') + ']:' +
		     _('RTPRIO'),		    "%s %s %s %s" % (_('Set thread scheduler tunables:'),
								     _('POLICY'),  _('and'), _('RTPRIO')))
	print fmt % ('-P, --show_threads',	    _('Show thread list'))
	print fmt % ('-q, --irqs=' + _('IRQ-LIST'), _('IRQ-LIST') + ' ' + _('affected by commands'))
	print fmt % ('-s, --save=' + _('FILENAME'), _('save kthreads sched tunables to') + ' ' + _('FILENAME'))
	print fmt % ('-S, --sockets=' +
		     _('CPU-SOCKET-LIST'),	    _('CPU-SOCKET-LIST') + ' ' + _('affected by commands'))
	print fmt % ('-t, --threads=' +
		     _('THREAD-LIST'),		    _('THREAD-LIST') + ' ' + _('affected by commands'))
	print fmt % ('-U, --no_uthreads',	    _('Operations will not affect user threads'))
	print fmt % ('-v, --version',		    _('Show version'))
	print fmt % ('-W, --what_is',		    _('Provides help about selected entities'))
	print fmt % ('-x, --spread',		    _('Spread selected entities over') + ' ' + _('CPU-LIST'))

def get_nr_cpus():
	global nr_cpus
	if nr_cpus:
		return nr_cpus
	nr_cpus = procfs.cpuinfo().nr_cpus
	return nr_cpus

def thread_help(tid):
	global ps
	if not ps:
		ps = procfs.pidstats()

	if not ps.has_key(tid):
		print "tuna: " + _("thread %d doesn't exists!") % tid
		return

	pinfo = ps[tid]
	cmdline = procfs.process_cmdline(pinfo)
	help, title = tuna.kthread_help_plain_text(tid, cmdline)
	print "%s\n\n%s" % (title, help)

def save(cpu_list, thread_list, filename):
	kthreads = tuna.get_kthread_sched_tunings()
	for name in kthreads.keys():
		kt = kthreads[name]
		if (cpu_list and not set(kt.affinity).intersection(set(cpu_list))) or \
		   (thread_list and kt.pid not in thread_list) :
			del kthreads[name]
	tuna.generate_rtgroups(filename, kthreads, get_nr_cpus())

def ps_show_header(has_ctxt_switch_info):
	print "%7s %6s %5s %7s       %s" % \
		(" ", " ", " ", _("thread"),
		 has_ctxt_switch_info and "ctxt_switches" or "")
	print "%7s %6s %5s %7s%s %15s" % \
		("pid", "SCHED_", "rtpri", "affinity",
		 has_ctxt_switch_info and " %9s %12s" % ("voluntary", "nonvoluntary") or "",
		 "cmd")

def ps_show_sockets(pid, ps, inodes, inode_re, indent = 0):
	header_printed = False
	dirname = "/proc/%s/fd" % pid
	try:
		filenames = os.listdir(dirname)
	except: # Process died
		return
	sindent = " " * indent
	for filename in filenames:
		pathname = os.path.join(dirname, filename)
		try:
			linkto = os.readlink(pathname)
		except: # Process died
			continue
		inode_match = inode_re.match(linkto)
		if not inode_match:
			continue
		inode = int(inode_match.group(1))
		if not inodes.has_key(inode):
			continue
		if not header_printed:
			print "%s%-10s %-6s %-6s %15s:%-5s %15s:%-5s" % \
			      (sindent, "State", "Recv-Q", "Send-Q",
			       "Local Address", "Port",
			       "Peer Address", "Port")
			header_printed = True
		s = inodes[inode]
		print "%s%-10s %-6d %-6d %15s:%-5d %15s:%-5d" % \
		      (sindent, s.state(),
		       s.receive_queue(), s.write_queue(),
		       s.saddr(), s.sport(), s.daddr(), s.dport())

def ps_show_thread(pid, affect_children, ps, cpuinfo, nics,
		   has_ctxt_switch_info, sock_inodes, sock_inode_re):
	global irqs
	try:
		affinity = schedutils.get_affinity(pid)
	except SystemError: # (3, 'No such process')
		return

	if len(affinity) <= 4:
		affinity = ",".join(str(a) for a in affinity)
	else:
		affinity = ",".join(str(hex(a)) for a in procfs.hexbitmask(affinity, cpuinfo.nr_cpus))
	sched = schedutils.schedstr(schedutils.get_scheduler(pid))[6:]
	rtprio = int(ps[pid]["stat"]["rt_priority"])
	cmd = ps[pid]["stat"]["comm"]
	users = ""
	if cmd[:4] == "IRQ-":
		try:
			if not irqs:
				irqs = procfs.interrupts()
			users = irqs[cmd[4:]]["users"]
			for u in users:
				if u in nics:
					users[users.index(u)] = "%s(%s)" % (u, ethtool.get_module(u))
			users = ",".join(users)
		except:
			users = "Not found in /proc/interrupts!"

	ctxt_switch_info = ""
	if has_ctxt_switch_info:
		voluntary_ctxt_switches = int(ps[pid]["status"]["voluntary_ctxt_switches"])
		nonvoluntary_ctxt_switches = int(ps[pid]["status"]["nonvoluntary_ctxt_switches"])
		ctxt_switch_info = " %9d %12s" % (voluntary_ctxt_switches,
						  nonvoluntary_ctxt_switches)
	
	if affect_children:
		print " %-5d " % pid,
	else:
		print "  %-5d" % pid,
	print "%6s %5d %8s%s %15s %s" % (sched, rtprio, affinity,
					 ctxt_switch_info, cmd, users)
	if sock_inodes:
		ps_show_sockets(pid, ps, sock_inodes, sock_inode_re,
				affect_children and 3 or 4)
	if affect_children and ps[pid].has_key("threads"):
		for tid in ps[pid]["threads"].keys():
			ps_show_thread(tid, False, ps[pid]["threads"],
				       cpuinfo, nics, has_ctxt_switch_info,
				       sock_inodes, sock_inode_re)
			

def ps_show(ps, affect_children, cpuinfo, thread_list, cpu_list,
	    irq_list_numbers, show_uthreads, show_kthreads,
	    has_ctxt_switch_info, sock_inodes, sock_inode_re):
				
	ps_list = []
	for pid in ps.keys():
		iskth = tuna.iskthread(pid)
		if not show_uthreads and not iskth:
			continue
		if not show_kthreads and iskth:
			continue
		in_irq_list = False
		if irq_list_numbers:
			if tuna.is_hardirq_handler(ps, pid):
				try:
					irq = int(ps[pid]["stat"]["comm"][4:])
					if irq not in irq_list_numbers:
						if not thread_list:
							continue
					else:
						in_irq_list = True
				except:
					pass
			elif not thread_list:
				continue
		if not in_irq_list and thread_list and pid not in thread_list:
			continue
		try:
			affinity = schedutils.get_affinity(pid)
		except SystemError: # (3, 'No such process')
			continue
		if cpu_list and not set(cpu_list).intersection(set(affinity)):
			continue
		ps_list.append(pid)

	ps_list.sort()

	nics = ethtool.get_active_devices()

	for pid in ps_list:
		ps_show_thread(pid, affect_children, ps, cpuinfo, nics,
			       has_ctxt_switch_info, sock_inodes,
			       sock_inode_re)

def load_socktype(socktype, inodes):
	idiag = inet_diag.create(socktype = socktype)
	while True:
		try:
			s = idiag.get()
		except:
			break
		inodes[s.inode()] = s

def load_sockets():
	inodes = {}
	for socktype in (inet_diag.TCPDIAG_GETSOCK,
			 inet_diag.DCCPDIAG_GETSOCK):
		load_socktype(socktype, inodes)
	return inodes

def do_ps(thread_list, cpu_list, irq_list, show_uthreads,
	  show_kthreads, affect_children, show_sockets):
	ps = procfs.pidstats()
	if affect_children:
		ps.reload_threads()

	sock_inodes = None
	sock_inode_re = None
	if show_sockets:
		sock_inodes = load_sockets()
		sock_inode_re = re.compile(r"socket:\[(\d+)\]")
	
	cpuinfo = procfs.cpuinfo()
	has_ctxt_switch_info = ps[1]["status"].has_key("voluntary_ctxt_switches")
	try:
		ps_show_header(has_ctxt_switch_info)
		ps_show(ps, affect_children, cpuinfo, thread_list,
			cpu_list, irq_list, show_uthreads, show_kthreads,
			has_ctxt_switch_info, sock_inodes, sock_inode_re)
	except IOError:
		# 'tuna -P | head' for instance
		pass

def do_list_op(op, current_list, op_list):
	if not current_list:
		current_list = []
	if op == '+':
		return list(set(current_list + op_list))
	if op == '-':
		return list(set(current_list) - set(op_list))
	return list(set(op_list))

def thread_mapper(s):
	global ps
	try:
		return [ int(s), ]
	except:
		pass
	if not ps:
		ps = procfs.pidstats()

	try:
		return ps.find_by_regex(re.compile(fnmatch.translate(s)))
	except:
		return ps.find_by_name(s)

def irq_mapper(s):
	global irqs
	try:
		return [ int(s), ]
	except:
		pass
	if not irqs:
		irqs = procfs.interrupts()

	irq_list_str = irqs.find_by_user_regex(re.compile(fnmatch.translate(s)))
	irq_list = []
	for i in irq_list_str:
		try:
			irq_list.append(int(i))
		except:
			pass
	return irq_list

def pick_op(argument):
	if argument[0] in ('+', '-'):
		return (argument[0], argument[1:])
	return (None, argument)

def i18n_init():
	(app, localedir) = ('tuna', '/usr/share/locale')
	locale.setlocale(locale.LC_ALL, '')
	gettext.bindtextdomain(app, localedir)
	gettext.textdomain(app)
	gettext.install(app, localedir)

def main():
	i18n_init()
	try:
		opts, args = getopt.getopt(sys.argv[1:],
					   "c:CfghiIKmnp:Pq:s:S:t:UvWx",
					   ("cpus=", "affect_children",
					    "filter", "gui", "help",
					    "isolate", "include",
					    "no_kthreads", "move",
					    "show_sockets", "priority=",
					    "show_threads", "irqs=",
					    "save=", "sockets=", "threads=",
					    "no_uthreads", "version", "what_is",
					    "spread"))
	except getopt.GetoptError, err:
		usage()
		print str(err)
		sys.exit(2)

	run_gui = not opts
	kthreads = True
	uthreads = True
	cpu_list = None
	irq_list = None
	irq_list_str = None
	thread_list = None
	thread_list_str = None
	filter = False
	affect_children = False
	show_sockets = False

	for o, a in opts:
		if o in ("-h", "--help"):
			usage()
			return
		elif o in ("-c", "--cpus"):
			(op, a) = pick_op(a)
			op_list = map(lambda cpu: int(cpu), a.split(","))
			cpu_list = do_list_op(op, cpu_list, op_list)
		elif o in ("-C", "--affect_children"):
			affect_children = True
		elif o in ("-t", "--threads"):
			(op, a) = pick_op(a)
			op_list = reduce(lambda i, j: i + j,
					 map(thread_mapper, a.split(",")))
			op_list = list(set(op_list))
			thread_list = do_list_op(op, thread_list, op_list)
			# Check if a process name was especified and no
			# threads was found, which would result in an empty
			# thread list, i.e. we would print all the threads
			# in the system when we should print nothing.
			if not op_list and type(a) == type(''):
				thread_list_str = do_list_op(op, thread_list_str,
							     a.split(","))
			if not op:
				irq_list = None
		elif o in ("-f", "--filter"):
			filter = True
		elif o in ("-g", "--gui"):
			run_gui = True
		elif o in ("-i", "--isolate"):
			if not cpu_list:
				print "tuna: --isolate " + _("requires a cpu list!")
				sys.exit(2)
			tuna.isolate_cpus(cpu_list, get_nr_cpus())
		elif o in ("-I", "--include"):
			if not cpu_list:
				print "tuna: --include " + _("requires a cpu list!")
				sys.exit(2)
			tuna.include_cpus(cpu_list, get_nr_cpus())
		elif o in ("-p", "--priority"):
			tuna.threads_set_priority(thread_list, a, affect_children)
		elif o in ("-P", "--show_threads"):
			# If the user specified process names that weren't
			# resolved to pids, don't show all threads.
			if not thread_list and not irq_list:
				if thread_list_str or irq_list_str:
					continue
			do_ps(thread_list, cpu_list, irq_list, uthreads,
			      kthreads, affect_children, show_sockets)
		elif o in ("-n", "--show_sockets"):
			show_sockets = True
		elif o in ("-m", "--move", "-x", "--spread"):
			if not cpu_list:
				print "tuna: --move " + _("requires a cpu list!")
				sys.exit(2)
			if not (thread_list or irq_list):
				print "tuna: --move " + _("requires a list of threads/irqs!")
				sys.exit(2)

			spread = o in ("-x", "--spread")

			if thread_list:
				tuna.move_threads_to_cpu(cpu_list, thread_list,
							 spread = spread)

			if irq_list:
				tuna.move_irqs_to_cpu(cpu_list, irq_list,
						      spread = spread)
		elif o in ("-s", "--save"):
			save(cpu_list, thread_list, a)
		elif o in ("-S", "--sockets"):
			(op, a) = pick_op(a)
			sockets = map(lambda socket: socket, a.split(","))

			if not cpu_list:
				cpu_list = []

			cpu_info = sysfs.cpus()
			op_list = []
			for socket in sockets:
				if not cpu_info.sockets.has_key(socket):
					print "tuna: %s %s" % \
					      (_("invalid socket"), socket,
					       _("sockets available: "),
					       ", ".join(cpu_info.sockets.keys()))
					sys.exit(2)
				op_list += [ int(cpu.name[3:]) for cpu in cpu_info.sockets[socket] ]
			cpu_list = do_list_op(op, cpu_list, op_list)
		elif o in ("-K", "--no_kthreads"):
			kthreads = False
		elif o in ("-q", "--irqs"):
			(op, a) = pick_op(a)
			op_list = reduce(lambda i, j: i + j,
					 map(irq_mapper, list(set(a.split(",")))))
			irq_list = do_list_op(op, irq_list, op_list)
			# See comment above about thread_list_str
			if not op_list and type(a) == type(''):
				irq_list_str = do_list_op(op, irq_list_str,
							  a.split(","))
			if not op:
				thread_list = None
		elif o in ("-U", "--no_uthreads"):
			uthreads = False
		elif o in ("-v", "--version"):
			print version
		elif o in ("-W", "--what_is"):
			if not thread_list:
				print "tuna: --what_is " + _("requires a thread list!")
				sys.exit(2)
			for tid in thread_list:
				thread_help(tid)

	if run_gui:
		try:
			from tuna import tuna_gui
		except ImportError:
			# gui packages not installed
			usage()
			return
		try:
			cpus_filtered = filter and cpu_list or []
			app = tuna_gui.main_gui(kthreads, uthreads, cpus_filtered)
			app.run()
		except KeyboardInterrupt:
			pass

if __name__ == '__main__':
    main()
