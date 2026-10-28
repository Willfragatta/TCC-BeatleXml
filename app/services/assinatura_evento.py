from cryptography.hazmat.primitives.serialization import pkcs12


def carregar_chave_certificado(pfx_bytes: bytes, senha: str):
    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        pfx_bytes, senha.encode()
    )
    if not private_key or not certificate:
        raise ValueError("Nao foi possivel carregar chave/certificado do PFX")
    return private_key, certificate


def assinar_xml_evento(
    xml_evento: str,
    pfx_bytes: bytes,
    senha: str,
    incluir_declaracao: bool = True,
) -> str:
    """
    Assina o infEvento (evento 210210) no XML conforme padrao NF-e.
    """
    try:
        from signxml import XMLSigner, methods, namespaces
        from lxml import etree
    except Exception as e:
        raise RuntimeError(f"Dependencias de assinatura nao instaladas: {e}")
    private_key, certificate = carregar_chave_certificado(pfx_bytes, senha)
    root = etree.fromstring(xml_evento.encode("utf-8"))
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    inf_evento = root.find(".//nfe:infEvento", namespaces=ns)
    if inf_evento is None:
        raise ValueError("infEvento nao encontrado para assinatura")

    evento_id = inf_evento.get("Id")
    if not evento_id:
        raise ValueError("Atributo Id ausente em infEvento")

    class NFeXMLSigner(XMLSigner):
        # NF-e exige SHA1, mas a lib bloqueia por padrao
        def check_deprecated_methods(self):
            return

    signer = NFeXMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha1",
        digest_algorithm="sha1",
        c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
    )
    # Usa namespace default para XMLDSIG (sem prefixo)
    signer.namespaces = {None: namespaces.ds}
    signed_result = signer.sign(
        inf_evento,
        key=private_key,
        cert=[certificate],
        reference_uri=f"#{evento_id}",
    )
    sig_list = signed_result.xpath(".//*[local-name()='Signature']")
    signature_el = sig_list[0] if sig_list else None
    if signature_el is None:
        raise ValueError("Signature nao encontrada apos assinatura")

    # Remove Signature do infEvento e anexa como irma em <evento>
    signature_el.getparent().remove(signature_el)
    parent_evento = inf_evento.getparent()
    parent_evento.replace(inf_evento, signed_result)
    parent_evento.append(signature_el)
    return etree.tostring(root, encoding="utf-8", xml_declaration=incluir_declaracao).decode("utf-8")
