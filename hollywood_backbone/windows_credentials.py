"""Read a Windows host secret through its verified current-user-only handle."""


def read_private_text(path):
    import win32api
    import win32con
    import win32file
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    handle = win32file.CreateFile(
        str(path), win32con.GENERIC_READ | 0x20000,
        win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING,
        0x00200000, None,
    )
    try:
        attributes = win32file.GetFileInformationByHandle(handle)[0]
        if attributes & (0x10 | 0x400):
            raise ValueError('Host secret must be an ordinary file, not a directory/reparse point')
        descriptor = win32security.GetSecurityInfo(
            handle, win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
        if descriptor.GetSecurityDescriptorOwner() != user:
            raise ValueError('Host secret belongs to another Windows account')
        acl = descriptor.GetSecurityDescriptorDacl()
        if acl is None or not acl.GetAceCount():
            raise ValueError('Host secret has no usable private DACL')
        for i in range(acl.GetAceCount()):
            ace = acl.GetAce(i)
            if ace[0][0] == win32security.ACCESS_DENIED_ACE_TYPE:
                continue
            if ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE or ace[2] != user:
                raise ValueError('Host secret grants access to another identity')
        _, value = win32file.ReadFile(handle, 4097)
        if len(value) > 4096:
            raise ValueError('Host secret exceeds its configured bound')
        return value.decode('utf-8').strip()
    finally:
        handle.Close()
