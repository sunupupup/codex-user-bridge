"""扫码登录协议与交互分离，测试可使用本地假服务器。"""
import time
from urllib.parse import urlencode

from .channel_base import ChannelError, json_request
from .weixin import DEFAULT_BASE, api_base, app_headers, post_headers, save_account, validate_account


def login(path, show_qr, read_code, report=print, base=DEFAULT_BASE, timeout=300):
    base = api_base(base)
    response = json_request(base + '/ilink/bot/get_bot_qrcode?bot_type=3',
                            {'local_token_list': []}, post_headers())
    qrcode, content = response.get('qrcode'), response.get('qrcode_img_content')
    if not isinstance(qrcode, str) or not qrcode or not isinstance(content, str) or not content:
        raise ValueError('微信没有返回有效二维码，请稍后重试。')
    show_qr(content)
    deadline, code = time.monotonic() + timeout, ''
    while time.monotonic() < deadline:
        query = {'qrcode': qrcode}
        if code:
            query['verify_code'] = code
        try:
            response = json_request(base + '/ilink/bot/get_qrcode_status?' + urlencode(query),
                                    headers=app_headers(), timeout=35)
        except ChannelError:
            report('扫码状态查询暂时失败，继续等待；未保存新凭据。')
            time.sleep(2)
            continue
        status = response.get('status')
        if status == 'confirmed':
            account = validate_account(response)
            save_account(path, account)
            return account
        if status == 'scaned_but_redirect':
            host = response.get('redirect_host', '')
            base = api_base(host if host.startswith('https://') else 'https://' + host)
        elif status == 'need_verifycode':
            code = read_code('输入手机上显示的配对验证码：').strip()
            if not code.isdigit() or len(code) > 12:
                raise ValueError('验证码应为数字，请重新运行登录助手。')
        elif status == 'scaned':
            code = ''
        elif status == 'binded_redirect':
            raise ValueError('此机器人已绑定其他客户端；请先解除绑定，或继续使用原有本地凭据。')
        elif status in ('expired', 'verify_code_blocked'):
            raise ValueError('二维码过期或验证被阻止，请重新运行登录助手。')
        elif status != 'wait':
            raise ValueError('微信返回未知登录状态，请检查客户端接入资格。')
        time.sleep(1)
    raise ValueError('扫码等待超时；未覆盖原有凭据，请重新运行。')
