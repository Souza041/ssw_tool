class SSWError(Exception):
    pass


class SSWLoginError(SSWError):
    pass


class SSWOperationError(SSWError):
    pass