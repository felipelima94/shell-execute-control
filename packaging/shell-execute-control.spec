Name:           shell-execute-control
Version:        %{package_version}
Release:        1
Summary:        Painel para executar comandos Bash e acompanhar seus logs
License:        LicenseRef-Not-Declared
URL:            https://github.com/felipelima94/shell-execute-control
BuildArch:      noarch
Requires:       bash, python3 >= 3.10, python3-tkinter, sudo

%description
Painel de APIs para iniciar e parar comandos Bash no desktop Linux e
acompanhar a saída padrão e os erros de cada execução.

%install
/bin/bash "%{source_dir}/packaging/stage.sh" "%{buildroot}"

%files
%defattr(-,root,root,-)
%dir /usr/lib/%{name}
/usr/lib/%{name}/*
%{_bindir}/%{name}
%{_datadir}/applications/%{name}.desktop
