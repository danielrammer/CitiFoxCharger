# Certificate generation uses Windows/.NET. Nothing is installed into a trust store.
# Private RSA parameters travel only through the captured child-process pipe.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$inputConfig = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ip = [Net.IPAddress]::Parse($inputConfig.host)
$rootKey = [Security.Cryptography.RSACryptoServiceProvider]::new(2048)
$serverKey = [Security.Cryptography.RSACryptoServiceProvider]::new(2048)
if ($rootKey -is [Security.Cryptography.RSACryptoServiceProvider]) { $rootKey.PersistKeyInCsp = $false }
if ($serverKey -is [Security.Cryptography.RSACryptoServiceProvider]) { $serverKey.PersistKeyInCsp = $false }
$rootCert = $null
$serverCert = $null
try {
    $hash = [Security.Cryptography.HashAlgorithmName]::SHA256
    $padding = [Security.Cryptography.RSASignaturePadding]::Pkcs1
    $rootRequest = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=CitiFoxCharger local access',$rootKey,$hash,$padding)
    $rootRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$true,0,$true))
    $rootRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign,$true))
    $rootSki = [Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($rootRequest.PublicKey,$false)
    $rootRequest.CertificateExtensions.Add($rootSki)
    # AuthorityKeyIdentifier extension (DER sequence containing context tag 0, 20-byte SKI).
    $identifier = [byte[]]::new(20)
    for ($i=0; $i -lt 20; $i++) { $identifier[$i] = [Convert]::ToByte($rootSki.SubjectKeyIdentifier.Substring($i*2,2),16) }
    $authority = [byte[]](@(0x30,0x16,0x80,0x14) + $identifier)
    $rootRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509Extension]::new([Security.Cryptography.Oid]::new('2.5.29.35'),$authority,$false))
    $start = [DateTimeOffset]::UtcNow.AddMinutes(-5)
    $end = [DateTimeOffset]::UtcNow.AddDays(730)
    $rootCert = $rootRequest.CreateSelfSigned($start,$end.AddDays(1))
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=CitiFoxCharger',$serverKey,$hash,$padding)
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false,$false,0,$true))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($request.PublicKey,$false))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509Extension]::new([Security.Cryptography.Oid]::new('2.5.29.35'),$authority,$false))
    $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $san.AddIpAddress($ip)
    $request.CertificateExtensions.Add($san.Build())
    $usages = [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new($usages,$true))
    $oids = [Security.Cryptography.OidCollection]::new()
    $null = $oids.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($oids,$false))
    $serial = [byte[]]::new(16)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($serial)
    $rng.Dispose()
    $serial[0] = $serial[0] -band 127
    $serverCert = $request.Create($rootCert,$start,$end,$serial)
    $parameters = $serverKey.ExportParameters($true)
    $numbers = @{}
    foreach ($name in @('Modulus','Exponent','D','P','Q','DP','DQ','InverseQ')) {
        $numbers[$name] = [Convert]::ToBase64String($parameters.$name)
    }
    @{
        root=[Convert]::ToBase64String($rootCert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
        certificate=[Convert]::ToBase64String($serverCert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
        key=$numbers
        expires=$end.ToString('o')
    } | ConvertTo-Json -Compress
} finally {
    if ($null -ne $serverCert) { $serverCert.Dispose() }
    if ($null -ne $rootCert) { $rootCert.Dispose() }
    $serverKey.Dispose()
    $rootKey.Dispose()
}
