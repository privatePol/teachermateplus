(function () {
  const navLinks = document.querySelectorAll('.fp-links a[href^="#"]');
  const navbarCollapse = document.getElementById("facultyPublicNav");

  navLinks.forEach((link) => {
    link.addEventListener("click", function () {
      if (window.innerWidth < 992 && navbarCollapse && navbarCollapse.classList.contains("show")) {
        const bsCollapse = bootstrap.Collapse.getOrCreateInstance(navbarCollapse);
        bsCollapse.hide();
      }
    });
  });
})();
