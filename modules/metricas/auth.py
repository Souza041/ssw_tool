def usuario_logado_id(request):
    user = request.session.get("user") or request.session.get("usuario")

    if isinstance(user, dict):
        return user.get("id")

    return None


def usuario_e_admin(request):
    user = request.session.get("user") or request.session.get("usuario")

    if not isinstance(user, dict):
        return False

    perfil = str(user.get("perfil") or user.get("role") or "").lower()

    return perfil in ["admin", "dev", "master"]