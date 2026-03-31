// Widget Betty embed - basé sur les 8 piliers (4 Arts + 4 Tech)
// Version robuste avec try/catch sur les entrées utilisateur
(function () {
  const root = document.getElementById("betty-chat-root");
  if (!root) return;

  const PILLARS = {
    ARTS: {
      MUSIQUE: {
        label: "Musique (Spotify + Bandcamp + remixes + TOP 100)",
        text:
          "Côté musique, Vincent Bastille propose plus de 400 titres : albums, EP club, remixes officiels et bandes originales, avec un vrai ancrage French Touch. Tu peux écouter sur Spotify, explorer les catalogues complets sur Bandcamp, voir sa bio… et accéder au TOP 100 French Touch & Electro qu’il a sélectionné.",
        links: [
          { label: "Écouter sur Spotify", url: "https://open.spotify.com/intl-fr/artist/2QS54zBHMU8zdaRs1pQRF0" },
          { label: "Bandcamp complet", url: "https://vincentbastille.bandcamp.com" },
          { label: "TOP 100 French Touch", url: "/top-100/" },
          { label: "Bio & discographie", url: "https://www.vincentbastille.online/" }
        ]
      },
      FILMS: {
        label: "Films & OST (Mystic, Le Lac, BO sur-mesure)",
        text:
          "Pour l’image : Mystic et Le Lac, plus un site dédié aux soundtracks pour le cinéma de genre et la danse contemporaine. Si tu cherches une BO sur-mesure ou du licensing pour un projet film/danse, c’est ici que ça se passe.",
        links: [
          { label: "Site OST films & danse", url: "https://vincentbastille-music-for-films-and.onrender.com/" },
          { label: "OST Mystic", url: "https://vincentbastille-music-for-films-and.onrender.com/mystic.html" },
          { label: "OST Le Lac", url: "https://vincentbastille-music-for-films-and.onrender.com/le-lac-ost.html" }
        ]
      },
      DANSEFLIX: {
        label: "DanseFlix (plateforme vidéo pros du spectacle)",
        text:
          "DanseFlix est une plateforme vidéo privée pour les professionnels du spectacle : compagnies, écoles, festivals. L’idée : captation multi-cam, montage, hébergement sécurisé et interface type plateforme de streaming pour valoriser vos créations et proposer une vraie expérience vidéo à votre public.",
        links: [
          { label: "Demander un devis création vidéo", url: "mailto:vincentbastille100@gmail.com?subject=Devis%20cr%C3%A9ation%20vid%C3%A9o%20DanseFlix" }
        ]
      },
      STUDIO: {
        label: "Studio de remix & projections vidéo",
        text:
          "Le studio de remix & projections vidéo s’adresse aux labels, musiciens et chorégraphes. Remixes sur mesure (club, radio, ciné) + scénographies vidéo projetées synchronisées avec ta musique ou ta chorégraphie.",
        links: [
          { label: "Commander un remix", url: "https://buy.stripe.com/4gM9ALbou5on0E3bTo3ks02" },
          { label: "Parler projections vidéo", url: "mailto:vincentbastille100@gmail.com?subject=Sc%C3%A9nographie%20vid%C3%A9o%20-%20Demande%20d%27infos" }
        ]
      }
    },
    TECH: {
      BETTY: {
        label: "Betty Bots (assistantes IA métiers)",
        text:
          "Betty Bots, ce sont des assistantes IA spécialisées par métier (avocat, médecin, restaurateur, immobilier, etc.). Elles accueillent tes visiteurs, posent les bonnes questions et t’envoient des leads propres par email. C’est le premier étage de la fusée pour une TPE ou un solo.",
        links: [
          { label: "Voir la page Betty Bots", url: "https://www.spectramedia.online/" },
          { label: "Fiche Betty dans l’annuaire IA", url: "https://spectraaidirectory.onrender.com/tool/1" }
        ]
      },
      DIRECTORY: {
        label: "Spectra AI Directory (annuaire AI)",
        text:
          "Spectra AI Directory est un annuaire d’outils IA sélectionnés à la main, orienté vrais usages business : support, vente, contenu, analytics… L’idée : que tu trouves rapidement les 2–3 bons outils autour de Betty ou SoloOrchestra AI sans te perdre dans le bruit.",
        links: [
          { label: "Voir les meilleurs outils IA", url: "https://spectra-media-directory-english-version.onrender.com/top-ai-tools" },
          { label: "Ajouter mon outil IA", url: "https://spectraaidirectory.onrender.com/add-tool" }
        ]
      },
      HAUBAN: {
        label: "Hauban IA Watch (veille IA quotidienne)",
        text:
          "Hauban IA Watch, c’est un rapport quotidien sur les dernières avancées en IA : modèles, usages business, signaux faibles. L’objectif : que tu restes à jour sans y passer 3 heures par jour, avec un résumé lisible et actionnable.",
        links: [
          { label: "Découvrir Hauban IA Watch", url: "https://spectramedia.gumroad.com/l/haubanai" }
        ]
      },
      ORCHESTRA: {
        label: "SoloOrchestra AI (multi-agents pour solos & TPE)",
        text:
          "SoloOrchestra AI est l’évolution d’Orchestra : un système d’agents IA coordonnés (vente, marketing, produit, veille) pour les solos et petites structures. Il sert à transformer tes notes d’idées en projets récurrents (comme Hauban, Broc&Bamp, etc.).",
        links: [
          { label: "Fiche Spectra Media (SoloOrchestra)", url: "https://www.f6s.com/company/spectra-media1" },
          { label: "Écrire à Vincent pour SoloOrchestra", url: "mailto:vincentbastille100@gmail.com?subject=Projet%20SoloOrchestra%20AI" }
        ]
      }
    }
  };

  function createEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  function renderWidget() {
    const launcher = createEl("button", "betty-launcher");
    const avatar = createEl("div", "betty-launcher-avatar", "B");
    const label = createEl("span", null, "Parler avec Betty");
    launcher.appendChild(avatar);
    launcher.appendChild(label);

    const chatWindow = createEl("div", "betty-chat-window");
    chatWindow.style.display = "none";

    const header = createEl("div", "betty-chat-header");
    const headerLeft = createEl("div", "betty-chat-header-left");
    const headerAvatar = createEl("div", "betty-chat-avatar", "B");
    const headerTextWrap = createEl("div");
    const headerTitle = createEl("div", "betty-chat-title", "Betty · Assistante IA Spectra");
    const headerSubtitle = createEl("div", "betty-chat-subtitle", "Basée sur les 8 piliers du site");
    headerTextWrap.appendChild(headerTitle);
    headerTextWrap.appendChild(headerSubtitle);
    headerLeft.appendChild(headerAvatar);
    headerLeft.appendChild(headerTextWrap);

    const closeBtn = createEl("button", "betty-chat-close", "×");
    closeBtn.setAttribute("aria-label", "Fermer la fenêtre Betty");

    header.appendChild(headerLeft);
    header.appendChild(closeBtn);

    const messages = createEl("div", "betty-chat-messages");
    const inputWrap = createEl("div", "betty-chat-input");
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Pose ta question ou choisis un pilier…";
    const sendBtn = document.createElement("button");
    sendBtn.textContent = "Envoyer";

    inputWrap.appendChild(input);
    inputWrap.appendChild(sendBtn);

    chatWindow.appendChild(header);
    chatWindow.appendChild(messages);
    chatWindow.appendChild(inputWrap);

    root.appendChild(chatWindow);
    root.appendChild(launcher);

    let state = {
      opened: false,
      lastCategory: null
    };

    function addMessage(text, from = "bot") {
      const msg = createEl("div", "betty-msg " + from);
      msg.textContent = text;
      messages.appendChild(msg);
      messages.scrollTop = messages.scrollHeight;
    }

    function addChoices() {
      const choicesWrap = createEl("div", "betty-choices");
      const groups = [
        { key: "ARTS", label: "🎵 Arts : musique, films, danse…" },
        { key: "TECH", label: "🤖 Technologie : Betty, annuaire AI, Hauban, SoloOrchestra…" }
      ];
      groups.forEach((g) => {
        const btn = createEl("button", "betty-choice", g.label);
        btn.addEventListener("click", () => {
          handleCategoryChoice(g.key);
        });
        choicesWrap.appendChild(btn);
      });
      messages.appendChild(choicesWrap);
      messages.scrollTop = messages.scrollHeight;
    }

    function addSubChoices(categoryKey) {
      const choicesWrap = createEl("div", "betty-choices");
      const items = PILLARS[categoryKey];
      Object.keys(items).forEach((k) => {
        const item = items[k];
        const btn = createEl("button", "betty-choice", item.label);
        btn.addEventListener("click", () => {
          handlePillarChoice(categoryKey, k);
        });
        choicesWrap.appendChild(btn);
      });
      messages.appendChild(choicesWrap);
      messages.scrollTop = messages.scrollHeight;
    }

    function handleCategoryChoice(categoryKey) {
      state.lastCategory = categoryKey;
      addMessage(
        categoryKey === "ARTS"
          ? "OK, on se concentre sur la partie Arts. Voici ce que je peux t’expliquer :"
          : "Parfait, on bascule côté Technologie. Voici les blocs que je peux détailler :",
        "bot"
      );
      addSubChoices(categoryKey);
    }

    function handlePillarChoice(categoryKey, pillarKey) {
      const pillar = PILLARS[categoryKey][pillarKey];
      addMessage(pillar.label, "user");
      addMessage(pillar.text, "bot");

      if (pillar.links && pillar.links.length) {
        const linkLines = pillar.links
          .map((l) => "• " + l.label)
          .join("\n");
        addMessage(
          "Je te recommande de commencer par :\n" +
            linkLines +
            "\nJe t’ouvre les liens dans de nouveaux onglets.",
          "bot"
        );
        pillar.links.forEach((l) => {
          window.open(l.url, "_blank");
        });
      }

      addMessage(
        "Tu peux me demander un autre pilier (Musique, Films & OST, DanseFlix, Studio, Betty Bots, annuaire AI, Hauban, SoloOrchestra), ou m’écrire ce que tu cherches (ex : “je suis prof de danse”, “j’ai un cabinet libéral”…).",
        "bot"
      );
    }

    function handleUserInput(text) {
      const trimmed = text.trim();
      if (!trimmed) return;
      addMessage(trimmed, "user");
      const lower = trimmed.toLowerCase();

      // Intention rapide
      if (lower.includes("musique") || lower.includes("spotify") || lower.includes("bandcamp") || lower.includes("top 100")) {
        return handlePillarChoice("ARTS", "MUSIQUE");
      }
      if (lower.includes("film") || lower.includes("ost") || lower.includes("mystic") || lower.includes("lac")) {
        return handlePillarChoice("ARTS", "FILMS");
      }
      if (lower.includes("danse") || lower.includes("danseflix")) {
        return handlePillarChoice("ARTS", "DANSEFLIX");
      }
      if (lower.includes("remix") || lower.includes("projection") || lower.includes("scénographie")) {
        return handlePillarChoice("ARTS", "STUDIO");
      }
      if (lower.includes("betty")) {
        return handlePillarChoice("TECH", "BETTY");
      }
      if (lower.includes("annuaire") || lower.includes("directory") || lower.includes("outil ia")) {
        return handlePillarChoice("TECH", "DIRECTORY");
      }
      if (lower.includes("hauban")) {
        return handlePillarChoice("TECH", "HAUBAN");
      }
      if (lower.includes("orchestra") || lower.includes("solorchestra") || lower.includes("solo orchestra")) {
        return handlePillarChoice("TECH", "ORCHESTRA");
      }

      // Profils types
      if (lower.includes("prof de danse") || lower.includes("école de danse") || lower.includes("compagnie")) {
        addMessage(
          "Tu es dans le monde de la danse / du spectacle : je te conseille de regarder DanseFlix (captation & plateforme vidéo) et les projections vidéo pour la scène. On peut aussi te paramétrer une Betty pour les inscriptions ou les demandes de devis.",
          "bot"
        );
        addSubChoices("ARTS");
        return;
      }

      if (lower.includes("tpe") || lower.includes("artisan") || lower.includes("commerce") || lower.includes("cabinet")) {
        addMessage(
          "Pour une TPE / artisan / cabinet, le point de départ le plus simple est Betty Bots (assistant métier) et l’annuaire AI pour compléter avec 2–3 outils pertinents autour.",
          "bot"
        );
        addSubChoices("TECH");
        return;
      }

      // Fallback : rediriger vers choix
      addMessage(
        "Je me base ici sur 8 piliers : Musique, Films & OST, DanseFlix, Studio de remix & projections, Betty Bots, annuaire AI, Hauban IA Watch, SoloOrchestra AI. Dis-moi lequel t’attire le plus, ou clique sur un bouton ci-dessous.",
        "bot"
      );
      addChoices();
    }

    // Version safe : on ne laisse pas le bot crasher silencieusement
    function safeHandleUserInput(text) {
      try {
        handleUserInput(text);
      } catch (e) {
        console.error("Erreur Betty widget :", e);
        addMessage(
          "Il y a eu un petit bug technique dans ma tête d’IA 😅. Tu peux cliquer sur un des boutons (Arts / Technologie ou un des 8 piliers) pendant que je me recale.",
          "bot"
        );
        addChoices();
      }
    }

    function openChat() {
      state.opened = true;
      chatWindow.style.display = "flex";
      launcher.style.display = "none";
      if (!messages.dataset.initialized) {
        messages.dataset.initialized = "1";
        addMessage("👋 Bonjour, moi c’est Betty. Je veille sur les 8 piliers de Spectra Media.", "bot");
        addMessage(
          "Cette page est la carte des projets : 4 blocs Arts (Musique, Films & OST, DanseFlix, Studio de remix & projections) et 4 blocs Technologie (Betty Bots, annuaire AI, Hauban IA Watch, SoloOrchestra AI).",
          "bot"
        );
        addMessage("Tu veux commencer par la partie Arts ou la partie Technologie ?", "bot");
        addChoices();
      }
    }

    function closeChat() {
      state.opened = false;
      chatWindow.style.display = "none";
      launcher.style.display = "inline-flex";
    }

    launcher.addEventListener("click", openChat);
    closeBtn.addEventListener("click", closeChat);

    sendBtn.addEventListener("click", function () {
      const value = input.value || "";
      input.value = "";
      safeHandleUserInput(value);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        const value = input.value || "";
        input.value = "";
        safeHandleUserInput(value);
      }
    });
  }

  renderWidget();
})();
