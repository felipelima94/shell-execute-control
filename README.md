# Painel de APIs

Aplicativo desktop para executar e acompanhar comandos Bash no Linux. Requer Bash
em `/bin/bash`, Python 3 e Tkinter para o Python do sistema.

Abra o painel a partir da pasta do projeto:

```sh
/usr/bin/python3 app.py
```

Para adicionar **Painel de APIs** ao menu de aplicativos do usuário:

```sh
/usr/bin/python3 install_launcher.py
```

O lançador usa o caminho atual do projeto. Execute o instalador novamente se
mover a pasta.

## Uso

1. Clique em **Novo comando**, informe o nome da API e cole o script completo.
   Com o diretório de trabalho vazio, a execução começa na sua pasta pessoal.
2. Use **Play** para iniciar cada API e **Ver log** para acompanhar stdout e
   stderr juntos. O painel mostra até os últimos 256 KiB e o arquivo guarda
   toda a saída da execução.
3. Use **Stop** para encerrar uma API. Ao fechar o painel, todos os comandos
   ativos recebem um pedido de encerramento ao mesmo tempo. Após cinco segundos,
   o painel força o encerramento dos grupos que ainda estiverem ativos.

Os comandos rodam em segundo plano para o painel. Mantenha a API no grupo
de processos do script para que o painel consiga encerrá-la. Se a execução
terminar por conta própria, o painel mostra o resultado e aguarda outro Play.

Os scripts são gravados em `${XDG_CONFIG_HOME:-~/.config}/shell-execute-control/commands.json`.
O log da última execução de cada comando fica em
`${XDG_STATE_HOME:-~/.local/state}/shell-execute-control/`. Os arquivos de
cadastro e de log têm permissões privadas do usuário. O próximo Play substitui
o log anterior.

## Comandos com sudo

Marque **Executar com sudo** no cadastro para rodar todo o script como root.
Se o sudo pedir autenticação, o painel abre uma caixa de senha e mostra a
conta indicada. A política do sistema define se a senha é do seu usuário ou
do root. Digite a senha nessa caixa. O painel só a envia ao sudo pelo canal
de autenticação.

O sudo precisa autorizar o Python do sistema a iniciar o supervisor do painel.
Uma regra que autoriza apenas o binário Java pode rejeitar essa chamada. Se o
sudo recusar a execução, **Ver log** mostra o motivo e deixa disponível o log
da última execução. **Stop** e o fechamento do painel usam a autorização da
execução já iniciada.
