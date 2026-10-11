'use strict';

import * as fs from 'fs';
import { cursor } from 'uci';
import { connect } from 'ubus';
import { init_enabled } from 'luci.sys';

const directory = '/etc/esurfing';
const credentials = directory + '/credentials';

function settings() {
	const uci = cursor();
	const result = uci.get_all('esurfing', 'main') || {};
	uci.unload();
	return result;
}

function managed(config) {
	return !config.credentials || config.credentials == credentials;
}

function valid_line(value) {
	return type(value) == 'string' && length(value) > 0 && length(value) <= 511 &&
		index(value, '\r') < 0 && index(value, '\n') < 0 && index(value, '\x00') < 0;
}

function read_credentials() {
	const parent = fs.lstat(directory);
	const st = fs.lstat(credentials);
	if (parent?.type != 'directory' || parent.uid != 0 || (parent.mode & 0o022) ||
		st?.type != 'file' || st.uid != 0 || (st.mode & 0o077) || st.size > 1024)
		return null;
	const text = fs.readfile(credentials, 1025);
	if (text == null) return null;
	const lines = split(text, '\n');
	if (lines[-1] == '') pop(lines);
	if (length(lines) != 2) return null;
	for (let i = 0; i < 2; i++) lines[i] = replace(lines[i], /\r$/, '');
	return valid_line(lines[0]) && valid_line(lines[1]) ? lines : null;
}

function account_info(config) {
	const editable = managed(config);
	const value = editable ? read_credentials() : null;
	// The password never leaves this backend, including on validation errors.
	return { managed: editable, configured: !!value, username: value?.[0] || '' };
}

function save_credentials(args) {
	if (!managed(settings())) return { error: 'custom_credentials' };
	if (!valid_line(args.username)) return { error: 'invalid_username' };
	let password = args.password;
	if (password == '') password = read_credentials()?.[1];
	if (!valid_line(password)) return { error: 'invalid_password' };
	// Match the C client's whole-file limit as well as its per-line limits.
	const content = args.username + '\n' + password + '\n';
	if (length(content) > 1024) return { error: 'credentials_too_long' };
	let st = fs.lstat(directory);
	if (!st) {
		if (!fs.mkdir(directory, 0o700)) return { error: 'write_failed' };
		st = fs.lstat(directory);
	}
	if (st?.type != 'directory' || st.uid != 0 || (st.mode & 0o022))
		return { error: 'unsafe_directory' };
	const existing = fs.lstat(credentials);
	if (existing && (existing.type != 'file' || existing.uid != 0))
		return { error: 'unsafe_credentials' };
	// A private directory plus exclusive creation prevents temporary-file and
	// symlink races. Rename within /etc/esurfing publishes both lines atomically.
	const temp = fs.mkdtemp(directory + '/.luci-XXXXXX');
	if (!temp) return { error: 'write_failed' };
	const path = temp + '/credentials';
	const file = fs.open(path, 'wx', 0o600);
	let ok = false;
	if (file) {
		ok = file.write(content) == length(content) && file.flush() == true;
		const closed = file.close();
		ok = ok && closed == true;
	}
	if (ok) ok = fs.rename(path, credentials) == true;
	if (!ok) fs.unlink(path);
	fs.rmdir(temp);
	return ok ? { saved: true } : { error: 'write_failed' };
}

return {
	'luci.esurfing': {
		status: {
			call: function() {
				const config = settings();
				const bus = connect();
				const service = bus?.call('service', 'list', { name: 'esurfing' });
				bus?.disconnect();
				const instance = service?.esurfing?.instances?.main;
				return {
					available: service != null,
					running: instance?.running == true,
					pid: instance?.running ? instance.pid : null,
					enabled: config.enabled == '1',
					autostart: init_enabled('esurfing'),
					account: account_info(config)
				};
			}
		},
		save_credentials: {
			args: { username: '', password: '' },
			call: function(request) { return save_credentials(request.args); }
		},
		action: {
			args: { action: '' },
			call: function(request) {
				const action = request.args.action;
				if (!(action in ['restart', 'stop', 'enable', 'disable']))
					return { error: 'invalid_action' };
				const config = settings();
				if (action == 'restart') {
					if (config.enabled != '1') return { error: 'service_disabled' };
					if (managed(config) && !read_credentials()) return { error: 'missing_credentials' };
				}
				// Some LuCI/ImmortalWrt versions do not export init_action.
				// Only the four actions validated above reach this fixed service.
				const pipe = fs.popen('/etc/init.d/esurfing ' + action + ' >/dev/null 2>&1', 'r');
				const result = pipe ? pipe.close() : null;
				return result === 0 ? { success: true } : { error: 'action_failed' };
			}
		},
		logs: {
			call: function() {
				// logread uses POSIX basic regex on the raw message (before timestamps).
				// Do not use -l: it limits all system messages before filtering.
				const pipe = fs.popen("/sbin/logread -e '^esurfing\\(\\[[0-9][0-9]*\\]\\)\\{0,1\\}:' 2>/dev/null", 'r');
				if (!pipe) return { error: 'logs_failed' };
				// Drain the finite snapshot, retaining only its last 32 KiB.
				let text = '', chunk;
				while (length(chunk = pipe.read(4096)))
					text = substr(text + chunk, -32768);
				const result = pipe.close();
				if (result !== 0) return { error: 'logs_failed' };
				const lines = split(text, '\n');
				if (lines[-1] == '') pop(lines);
				return { text: join('\n', slice(lines, -100)) };
			}
		}
	}
};
