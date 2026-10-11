'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require ui';
'require poll';

var callStatus = rpc.declare({ object: 'luci.esurfing', method: 'status', expect: { '': {} }, reject: true });
var callAction = rpc.declare({ object: 'luci.esurfing', method: 'action', params: ['action'], expect: { '': {} }, reject: true });
var callSaveCredentials = rpc.declare({ object: 'luci.esurfing', method: 'save_credentials', params: ['username', 'password'], expect: { '': {} }, reject: true });
var callLogs = rpc.declare({ object: 'luci.esurfing', method: 'logs', expect: { '': {} }, reject: true });

function checked(result) {
	var errors = {
		custom_credentials: '当前使用自定义凭据路径，请在命令行管理账号。',
		invalid_username: '请输入账号，不能包含换行，UTF-8 长度最多 511 字节。',
		invalid_password: '请输入密码；只有已保存有效密码时才能留空。',
		credentials_too_long: '账号与密码合计过长。',
		unsafe_directory: '凭据目录的所有者或权限不正确，请检查 /etc/esurfing。',
		unsafe_credentials: '凭据文件不是 root 拥有的普通文件，请在命令行检查。',
		write_failed: '账号保存失败，请检查剩余空间及凭据目录权限。',
		service_disabled: '请先勾选“启用服务”并保存应用，再启动服务。',
		missing_credentials: '请先保存有效的账号密码。',
		invalid_action: '不支持的服务操作。',
		action_failed: '服务操作失败，请检查系统日志。',
		logs_failed: '无法读取服务日志。'
	};
	if (!result || result.error)
		throw new Error(errors[result && result.error] || '服务未返回有效结果，请检查 rpcd。');
	return result;
}

function validCredential(value, allowEmpty) {
	return (allowEmpty || value.length > 0) && !/[\r\n\x00]/.test(value) &&
		new TextEncoder().encode(value).length <= 511;
}

function validEndpoint(value) {
	if (!value) return true;
	try {
		var url = new URL(value);
		return /^https?:\/\//i.test(value) && /^[\x21-\x7e]+$/.test(value) && value.length < 4096 &&
			!!url.hostname && !url.username && !url.password && value.indexOf('#') < 0 &&
			value.split('/')[2].indexOf('@') < 0;
	} catch (error) { return false; }
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('esurfing'), uci.load('network'), callStatus()]);
	},

	refreshStatus: function() {
		return callStatus().then(L.bind(function(data) {
			this.status = checked(data);
			this.statusNode.textContent = !data.available ? '无法读取进程状态' :
				data.running ? '进程运行中 · PID ' + data.pid : '进程未运行';
			this.bootNode.textContent = '开机启动：' + (data.autostart ? '已开启' : '已关闭');
			this.bootButton.textContent = data.autostart ? '关闭开机启动' : '开启开机启动';
			this.accountNode.textContent = !data.account.managed ? '账号：使用自定义凭据文件' :
				data.account.configured ? '账号：' + data.account.username : '账号：尚未配置或文件无效';
			this.accountButton.disabled = this.readonly || !data.account.managed;
		}, this)).catch(L.bind(function(error) {
			this.statusNode.textContent = '状态读取失败：' + error.message;
			throw error;
		}, this));
	},

	serviceAction: function(action) {
		return callAction(action).then(checked).then(L.bind(function(result) {
			if (!result.success) throw new Error('服务未确认操作成功。');
			return this.refreshStatus();
		}, this)).then(L.bind(function() {
			ui.addNotification(null, E('p', '操作已提交。服务状态会自动刷新。'), 'info');
		}, this)).catch(function(error) {
			ui.addNotification(null, E('p', error.message), 'error');
		});
	},

	editAccount: function() {
		var account = this.status.account;
		var username = E('input', { 'class': 'cbi-input-text', 'type': 'text',
			'autocomplete': 'off', 'aria-label': '校园网账号', 'value': account.username || '' });
		var password = E('input', { 'class': 'cbi-input-password', 'type': 'password',
			'autocomplete': 'new-password', 'aria-label': '校园网密码',
			'placeholder': account.configured ? '留空保留原密码' : '请输入密码' });
		var errorNode = E('p', { 'class': 'alert-message warning', 'style': 'display:none' });
		var cancel = E('button', { 'class': 'btn', 'click': function() {
			password.value = ''; ui.hideModal();
		} }, '取消');
		var save = E('button', { 'class': 'btn cbi-button-positive', 'click': ui.createHandlerFn(this, function() {
			if (!validCredential(username.value, false) || !validCredential(password.value, account.configured)) {
				errorNode.textContent = '账号和密码不能包含换行，每项最多 511 个 UTF-8 字节；首次配置必须填写密码。';
				errorNode.style.display = '';
				return;
			}
			cancel.disabled = true;
			return callSaveCredentials(username.value, password.value).then(checked).then(L.bind(function(result) {
				if (!result.saved) throw new Error('账号未保存。');
				password.value = '';
				ui.hideModal();
				ui.addNotification(null, E('p', '账号已保存。点击“启动 / 重新登录”使用新凭据。'), 'info');
				return this.refreshStatus();
			}, this)).catch(function(error) {
				errorNode.textContent = error.message;
				errorNode.style.display = '';
			}).finally(function() { cancel.disabled = false; });
		}) }, '保存账号');
		ui.showModal('校园网账号', [
			E('p', '密码不会回显，留空保留原密码。保存后将在下一次登录使用，不受页面“重置”影响。'),
			E('p', [E('label', ['账号 ', username])]),
			E('p', [E('label', ['密码 ', password])]), errorNode,
			E('div', { 'class': 'right' }, [cancel, ' ', save])
		]);
		username.focus();
	},

	refreshLogs: function() {
		return callLogs().then(checked).then(L.bind(function(result) {
			// Use textContent so that log messages can never inject page markup.
			this.logsNode.textContent = result.text || '暂无日志。';
		}, this)).catch(L.bind(function(error) {
			this.logsNode.textContent = error.message;
		}, this));
	},

	render: function(data) {
		this.status = checked(data[2]);
		this.readonly = !L.hasViewPermission();
		var m = new form.Map('esurfing', '天翼校园',
			'精简 C 客户端。首次使用：保存账号，勾选启用服务并保存应用，再启动服务；需要时开启开机启动。');
		var s = m.section(form.NamedSection, 'main', 'esurfing');
		s.addremove = false;
		s.tab('basic', '基本设置');
		s.tab('retry', '自动重试');
		s.tab('advanced', '高级设置');
		var o = s.taboption('basic', form.Flag, 'enabled', '启用服务', '保存应用会重新加载服务配置。');
		o.rmempty = false;
		o = s.taboption('basic', form.Value, 'interface', '校园网接口', '填写逻辑接口名称，通常为 wan。');
		o.default = 'wan'; o.rmempty = false; o.datatype = 'uciname';
		uci.sections('network', 'interface').forEach(function(section) {
			if (section['.name'] != 'loopback') o.value(section['.name']);
		});
		o = s.taboption('retry', form.Value, 'retry_delay', '初始等待（秒）');
		o.default = '5'; o.rmempty = false; o.datatype = 'range(1,86400)';
		o = s.taboption('retry', form.Value, 'retry_max', '最长等待（秒）',
			'失败后逐次加倍并加入少量抖动。服务器指定的 Retry-After 可以超过此值。');
		o.default = '300'; o.rmempty = false; o.datatype = 'range(1,86400)';
		o.validate = function(section, value) {
			var initial = this.map.lookupOption('retry_delay', section)[0].formvalue(section);
			return Number(value) >= Number(initial) || '最长等待不能小于初始等待。';
		};
		o = s.taboption('advanced', form.Value, 'device', '固定设备', '通常留空，由逻辑接口自动确定，例如 eth0.2。');
		o.validate = function(section, value) {
			return !value || /^[a-zA-Z0-9_.:-]{1,15}$/.test(value) || '请输入最多 15 字符的设备名，例如 eth0.2。';
		};
		o = s.taboption('advanced', form.Value, 'gateway', '网关覆盖', '留空自动读取 IPv4 默认网关。');
		o.datatype = 'ip4addr("nomask")';
		[['portal_url', '门户入口'], ['ticket_url', 'Ticket 地址'], ['auth_url', '认证地址']].forEach(function(field) {
			o = s.taboption('advanced', form.Value, field[0], field[1],
				field[0] == 'portal_url' ? '通常留空，使用程序默认入口。' : '仅在需要时填写；Ticket 与认证地址必须同时设置。');
			o.validate = function(section, value) {
				if (!validEndpoint(value))
					return '请输入有效的 HTTP/HTTPS 地址（小于 4096 字节，不含空格、中文、账号信息或 # 片段）。';
				if (field[0] != 'portal_url') {
					var other = this.map.lookupOption(field[0] == 'ticket_url' ? 'auth_url' : 'ticket_url', section)[0].formvalue(section);
					if (!!value != !!other) return 'Ticket 与认证地址必须同时设置或同时留空。';
				}
				return true;
			};
		});
		this.statusNode = E('strong', '读取状态中…');
		this.bootNode = E('span');
		this.accountNode = E('span');
		this.accountButton = E('button', { 'class': 'btn', 'disabled': this.readonly || null,
			'click': ui.createHandlerFn(this, 'editAccount') }, '配置账号');
		this.bootButton = E('button', { 'class': 'btn', 'disabled': this.readonly || null,
			'click': ui.createHandlerFn(this, function() { return this.serviceAction(this.status.autostart ? 'disable' : 'enable'); }) }, '开机启动');
		this.logsNode = E('pre', { 'style': 'max-height:22em;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere' }, '点击“刷新日志”读取最近日志。');
		var panel = E('div', { 'class': 'cbi-section' }, [
			E('h3', '运行状态'), E('p', {}, this.statusNode), E('p', {}, this.accountNode), E('p', {}, this.bootNode),
			E('p', '进程运行不代表认证成功。账号拒绝或协议不支持时会暂停，请检查日志；修正后点击“启动 / 重新登录”。'),
			E('div', { 'class': 'cbi-section-actions', 'style': 'display:flex;flex-wrap:wrap;gap:.5em' }, [
				this.accountButton,
				E('button', { 'class': 'btn cbi-button-positive', 'disabled': this.readonly || null,
					'click': ui.createHandlerFn(this, function() { return this.serviceAction('restart'); }) }, '启动 / 重新登录'),
				E('button', { 'class': 'btn cbi-button-negative', 'disabled': this.readonly || null,
					'click': ui.createHandlerFn(this, function() { return this.serviceAction('stop'); }) }, '停止'), this.bootButton
			])
		]);
		return m.render().then(L.bind(function(map) {
			poll.add(L.bind(function() {
				if (!document.hidden) return this.refreshStatus().catch(function() {});
			}, this), 10);
			this.refreshStatus().catch(function() {});
			return E('div', [map, panel, E('div', { 'class': 'cbi-section' }, [
				E('h3', '服务日志'), E('p', '按需读取最近 100 条匹配日志，最多显示 32 KiB。'),
				E('button', { 'class': 'btn', 'click': ui.createHandlerFn(this, 'refreshLogs') }, '刷新日志'), this.logsNode
			])]);
		}, this));
	}
});
